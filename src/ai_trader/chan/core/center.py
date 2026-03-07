from __future__ import annotations

from typing import cast

from ai_trader.types import Bi, Segment, Zhongshu, ZhongshuEvolution


def _build_center_from_three_segments(
    s1: Segment, s2: Segment, s3: Segment
) -> Zhongshu | None:
    zd = max(s1.low, s2.low, s3.low)
    zg = min(s1.high, s2.high, s3.high)
    if zd > zg:
        return None

    highs = [s1.high, s2.high, s3.high]
    lows = [s1.low, s2.low, s3.low]

    return Zhongshu(
        zd=zd,
        zg=zg,
        gg=max(highs),
        dd=min(lows),
        g=min(highs),
        d=max(lows),
        start_index=s1.start_index,
        end_index=s3.end_index,
        event_time=s3.event_time,
        available_time=max(s1.available_time, s2.available_time, s3.available_time),
        evolution="newborn",
        status="confirmed",
    )


def _build_center_from_three_bis(b1: Bi, b2: Bi, b3: Bi) -> Zhongshu | None:
    """Build a zhongshu from three consecutive bis (the minimal level)."""
    zd = max(b1.low, b2.low, b3.low)
    zg = min(b1.high, b2.high, b3.high)
    if zd > zg:
        return None

    highs = [b1.high, b2.high, b3.high]
    lows = [b1.low, b2.low, b3.low]

    return Zhongshu(
        zd=zd,
        zg=zg,
        gg=max(highs),
        dd=min(lows),
        g=min(highs),
        d=max(lows),
        start_index=b1.start_index,
        end_index=b3.end_index,
        event_time=b3.event_time,
        available_time=max(b1.available_time, b2.available_time, b3.available_time),
        evolution="newborn",
        status="confirmed",
    )


def _overlap_center(a: Zhongshu, b: Zhongshu) -> bool:
    """Check if two centers' 中枢区间 (zd/zg) overlap."""
    return max(a.zd, b.zd) <= min(a.zg, b.zg)


def _wave_ranges_touch(prev: Zhongshu, cur: Zhongshu) -> bool:
    """Check if the oscillation ranges (dd~gg) of two centers touch.

    kline8-20: 围绕该中枢产生的波动触及前面走势中枢延续时的某个
    瞬间波动区间 → 级别扩张, not trend.
    """
    return max(prev.dd, cur.dd) <= min(prev.gg, cur.gg)


def _merge_two_centers(prev: Zhongshu, cur: Zhongshu) -> Zhongshu:
    """Merge two centers into a larger-level center (level expansion).

    The merged center's zd/zg is the intersection of the combined
    oscillation ranges, and gg/dd spans everything.
    """
    return Zhongshu(
        zd=min(prev.zd, cur.zd),
        zg=max(prev.zg, cur.zg),
        gg=max(prev.gg, cur.gg),
        dd=min(prev.dd, cur.dd),
        g=min(prev.g, cur.g),
        d=max(prev.d, cur.d),
        start_index=prev.start_index,
        end_index=cur.end_index,
        event_time=cur.event_time,
        available_time=max(prev.available_time, cur.available_time),
        evolution="expansion",
        status="confirmed",
    )


def _evolve_and_append(out: list[Zhongshu], candidate: Zhongshu) -> None:
    """Determine the relationship between candidate and the last zhongshu,
    then either extend, merge (expansion), or append as newborn.
    """
    if not out:
        out.append(candidate)
        return

    prev = out[-1]

    # Case 1: 中枢区间 overlap → extension (same center continues)
    if _overlap_center(prev, candidate):
        prev.zd = max(prev.zd, candidate.zd)
        prev.zg = min(prev.zg, candidate.zg)
        prev.gg = max(prev.gg, candidate.gg)
        prev.dd = min(prev.dd, candidate.dd)
        prev.g = min(prev.g, candidate.g)
        prev.d = max(prev.d, candidate.d)
        prev.end_index = candidate.end_index
        prev.event_time = candidate.event_time
        prev.available_time = candidate.available_time
        prev.evolution = "extension"
        return

    # Case 2: 中枢区间 no overlap but wave ranges touch → expansion
    # (kline8-20: level upgrade, merge into bigger center)
    if _wave_ranges_touch(prev, candidate):
        merged = _merge_two_centers(prev, candidate)
        out[-1] = merged
        return

    # Case 3: completely separate → newborn
    candidate.evolution = cast(ZhongshuEvolution, "newborn")
    out.append(candidate)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_zhongshus(segments: list[Segment]) -> list[Zhongshu]:
    """Build zhongshus from segments (segment-level centers).

    Kept for backward compatibility. Use build_zhongshus_from_bis for
    the more granular bi-level centers.
    """
    out: list[Zhongshu] = []
    if len(segments) < 3:
        return out

    for i in range(2, len(segments)):
        candidate = _build_center_from_three_segments(
            segments[i - 2], segments[i - 1], segments[i]
        )
        if candidate is None:
            continue
        _evolve_and_append(out, candidate)

    return out


def build_zhongshus_from_bis(bis: list[Bi]) -> list[Zhongshu]:
    """Build zhongshus from bis (bi-level centers).

    This is the correct minimal-level center construction per kline8-18:
    "被至少三个连续次级别走势类型所重叠的部分".
    At the lowest level, bis are the elementary sub-level moves.
    """
    out: list[Zhongshu] = []
    if len(bis) < 3:
        return out

    i = 0
    while i + 2 < len(bis):
        candidate = _build_center_from_three_bis(bis[i], bis[i + 1], bis[i + 2])
        if candidate is None:
            i += 1
            continue

        # Try to extend with subsequent bis (center extension up to 9 bis
        # per kline8-33: "中枢的延伸不能超过5段" → 3 initial + 6 extension = 9 max)
        j = i + 3
        extension_count = 0
        max_extensions = 6  # 5 extra segments beyond the initial 3
        while j < len(bis) and extension_count < max_extensions:
            bi = bis[j]
            if bi.low <= candidate.zg and bi.high >= candidate.zd:
                # This bi overlaps the center → extend
                candidate.zd = max(candidate.zd, bi.low)
                candidate.zg = min(candidate.zg, bi.high)
                candidate.gg = max(candidate.gg, bi.high)
                candidate.dd = min(candidate.dd, bi.low)
                candidate.g = min(candidate.g, bi.high)
                candidate.d = max(candidate.d, bi.low)
                candidate.end_index = bi.end_index
                candidate.event_time = bi.event_time
                candidate.available_time = max(
                    candidate.available_time, bi.available_time
                )
                extension_count += 1
                j += 1
            else:
                break

        _evolve_and_append(out, candidate)
        i = j  # skip past the consumed bis

    return out
