from __future__ import annotations

from typing import Literal, cast

from ai_trader.types import Bi, Segment, Zhongshu, ZhongshuEvolution

CenterRelation = Literal[
    "extension",
    "trend_up",
    "trend_down",
    "expansion",
    "separate",
]


def _build_center_from_three_segments(
    s1: Segment, s2: Segment, s3: Segment
) -> Zhongshu | None:
    if any(item.status != "confirmed" for item in (s1, s2, s3)):
        return None
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
        origin_available_time=max(s1.available_time, s2.available_time, s3.available_time),
        evolution="newborn",
        status="confirmed",
    )


def _build_center_from_three_bis(b1: Bi, b2: Bi, b3: Bi) -> Zhongshu | None:
    """Build a zhongshu from three consecutive bis (the minimal level)."""
    if any(item.status != "confirmed" for item in (b1, b2, b3)):
        return None
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
        origin_available_time=max(b1.available_time, b2.available_time, b3.available_time),
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


def _extend_with_z_move(center: Zhongshu, move: Bi | Segment) -> None:
    """Extend a center with the next completed same-direction Z move."""
    center.evolution = "extension"
    center.gg = max(center.gg, move.high)
    center.dd = min(center.dd, move.low)
    center.g = min(center.g, move.high)
    center.d = max(center.d, move.low)
    center.end_index = move.end_index
    center.event_time = move.event_time
    center.available_time = max(center.available_time, move.available_time)


def classify_center_relation(prev: Zhongshu, cur: Zhongshu) -> CenterRelation:
    """Classify the relation between two consecutive same-level centers.

    Chan 中枢定理里，趋势延续和级别扩展不能只看 [ZD, ZG] 是否分离，
    还必须结合波动区间 [DD, GG]。
    """
    if _overlap_center(prev, cur):
        return "extension"
    if cur.dd > prev.gg:
        return "trend_up"
    if cur.gg < prev.dd:
        return "trend_down"
    if cur.zd > prev.zg and cur.dd <= prev.gg:
        return "expansion"
    if cur.zg < prev.zd and cur.gg >= prev.dd:
        return "expansion"
    if _wave_ranges_touch(prev, cur):
        return "expansion"
    return "separate"


def _evolve_and_append(out: list[Zhongshu], candidate: Zhongshu) -> None:
    """Determine the relationship between candidate and the last zhongshu,
    then either extend, mark expansion, or append as newborn.
    """
    if not out:
        out.append(candidate)
        return

    prev = out[-1]
    relation = classify_center_relation(prev, candidate)

    # Case 1: 中枢区间 overlap → extension (same center continues)
    if relation == "extension":
        # [ZD, ZG] is fixed by the first three completed sub-level moves.
        # Later overlapping moves extend the same center, but they do not
        # redefine (shrink) its core interval.  Only the surrounding range
        # statistics and the temporal boundary evolve.
        prev.gg = max(prev.gg, candidate.gg)
        prev.dd = min(prev.dd, candidate.dd)
        prev.g = min(prev.g, candidate.g)
        prev.d = max(prev.d, candidate.d)
        prev.end_index = candidate.end_index
        prev.event_time = candidate.event_time
        prev.available_time = candidate.available_time
        prev.evolution = "extension"
        return

    # Case 2: 级别扩张。保留当前级别的中心序列，不把它们塌缩成一个
    # 人造的大中枢，否则后续趋势和三类买卖点判断会失真。
    #
    # Preserve the current-level center sequence instead of collapsing it
    # into a synthetic larger center.  Kline8-20 treats this as 级别扩张,
    # which means the previous and current centers imply a higher-level
    # structure, but they should not erase the current-level history that
    # later B3 / trend checks rely on.
    if relation == "expansion":
        candidate.evolution = cast(ZhongshuEvolution, "expansion")
        out.append(candidate)
        return

    # Case 3: completely separate or confirmed same-direction trend →
    # append a new same-level center.
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

    i = 0
    while i + 2 < len(segments):
        candidate = _build_center_from_three_segments(
            segments[i], segments[i + 1], segments[i + 2]
        )
        if candidate is None:
            i += 1
            continue

        # Z1 and Z2 are the first and third moves.  Later Z moves therefore
        # occur at i+4, i+6, ...; the intervening reverse move alone cannot
        # confirm a center extension.
        j = i + 4
        while j < len(segments):
            z_move = segments[j]
            if z_move.status != "confirmed":
                break
            if z_move.low > candidate.zg or z_move.high < candidate.zd:
                break
            _extend_with_z_move(candidate, z_move)
            j += 2

        _evolve_and_append(out, candidate)

        if j < len(segments):
            # The non-overlapping Z move starts the search for a new center;
            # the reverse move immediately before it is the connector out of
            # the completed center.  Reusing the last in-center Z move would
            # force consecutive centers to share a source move, making their
            # wave ranges touch by construction and making a trend impossible.
            i = j
        else:
            break

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

        # Z1 and Z2 are bis[i] and bis[i+2].  Only the next completed
        # same-direction move (i+4, i+6, ...) can confirm extension; the
        # intervening reverse bi is merely the connector.
        j = i + 4
        while j < len(bis):
            z_move = bis[j]
            if z_move.status != "confirmed":
                break
            if z_move.low > candidate.zg or z_move.high < candidate.zd:
                break
            _extend_with_z_move(candidate, z_move)
            j += 2

        _evolve_and_append(out, candidate)
        if j < len(bis):
            # Start the next-center search at the first non-overlapping Z bi;
            # the preceding reverse bi is the connector between centers.
            i = j
        else:
            break

    return out
