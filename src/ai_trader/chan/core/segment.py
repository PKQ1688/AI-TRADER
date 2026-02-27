from __future__ import annotations

from dataclasses import dataclass

from ai_trader.types import Bi, Segment


@dataclass(slots=True)
class FeatureBar:
    high: float
    low: float
    source_idx: list[int]


def _overlap(low1: float, high1: float, low2: float, high2: float) -> bool:
    return max(low1, low2) <= min(high1, high2)


def _has_three_overlap(b1: Bi, b2: Bi, b3: Bi) -> bool:
    low = max(b1.low, b2.low, b3.low)
    high = min(b1.high, b2.high, b3.high)
    return low <= high


def _merge_feature_bars(bars: list[FeatureBar]) -> list[FeatureBar]:
    if len(bars) < 2:
        return bars[:]

    merged = [bars[0]]
    direction = 0

    for cur in bars[1:]:
        prev = merged[-1]
        include = (prev.high >= cur.high and prev.low <= cur.low) or (prev.high <= cur.high and prev.low >= cur.low)
        if not include:
            if cur.high > prev.high and cur.low > prev.low:
                direction = 1
            elif cur.high < prev.high and cur.low < prev.low:
                direction = -1
            merged.append(cur)
            continue

        use_dir = direction
        if use_dir == 0:
            if cur.high >= prev.high and cur.low >= prev.low:
                use_dir = 1
            elif cur.high <= prev.high and cur.low <= prev.low:
                use_dir = -1
            else:
                use_dir = 1

        if use_dir >= 0:
            high = max(prev.high, cur.high)
            low = max(prev.low, cur.low)
        else:
            high = min(prev.high, cur.high)
            low = min(prev.low, cur.low)

        merged[-1] = FeatureBar(high=high, low=low, source_idx=prev.source_idx + cur.source_idx)

    return merged


def _find_feature_fractal(std: list[FeatureBar], want: str) -> int | None:
    for i in range(1, len(std) - 1):
        left, mid, right = std[i - 1], std[i], std[i + 1]
        is_top = mid.high > left.high and mid.high > right.high and mid.low > left.low and mid.low > right.low
        is_bottom = mid.low < left.low and mid.low < right.low and mid.high < left.high and mid.high < right.high
        if want == "top" and is_top:
            return i
        if want == "bottom" and is_bottom:
            return i
    return None


def _feature_peak_index(mid: FeatureBar, bis: list[Bi], want: str) -> int:
    peak_idx = mid.source_idx[0]
    if want == "top":
        peak_val = bis[peak_idx].high
        for idx in mid.source_idx[1:]:
            if bis[idx].high >= peak_val:
                peak_val = bis[idx].high
                peak_idx = idx
        return peak_idx

    peak_val = bis[peak_idx].low
    for idx in mid.source_idx[1:]:
        if bis[idx].low <= peak_val:
            peak_val = bis[idx].low
            peak_idx = idx
    return peak_idx


def _reverse_confirm(bis: list[Bi], peak_idx: int, seg_dir: str) -> bool:
    reverse_seg_dir = "down" if seg_dir == "up" else "up"
    feature_dir = seg_dir
    feature = [
        FeatureBar(high=bi.high, low=bi.low, source_idx=[i])
        for i, bi in enumerate(bis)
        if i > peak_idx and bi.direction == feature_dir
    ]
    if len(feature) < 3:
        return False

    std = _merge_feature_bars(feature)
    want = "bottom" if reverse_seg_dir == "down" else "top"
    return _find_feature_fractal(std, want) is not None


def _find_segment_end(
    bis: list[Bi],
    start_idx: int,
    require_case2_confirmation: bool,
) -> tuple[int | None, str | None]:
    seg_dir = bis[start_idx].direction
    feature_dir = "down" if seg_dir == "up" else "up"
    want_fx = "top" if seg_dir == "up" else "bottom"

    feature: list[FeatureBar] = []

    for idx in range(start_idx, len(bis)):
        bi = bis[idx]
        if bi.direction != feature_dir:
            continue

        feature.append(FeatureBar(high=bi.high, low=bi.low, source_idx=[idx]))
        if len(feature) < 3:
            continue

        std = _merge_feature_bars(feature)
        fx_idx = _find_feature_fractal(std, want_fx)
        if fx_idx is None:
            continue

        first = std[fx_idx - 1]
        second = std[fx_idx]
        peak_idx = _feature_peak_index(second, bis, want_fx)

        has_gap = not _overlap(first.low, first.high, second.low, second.high)
        if not has_gap:
            return peak_idx, "case1"

        if not require_case2_confirmation or _reverse_confirm(bis, peak_idx, seg_dir):
            return peak_idx, "case2"

    return None, None


def build_segments(bis: list[Bi], require_case2_confirmation: bool = True) -> list[Segment]:
    segments: list[Segment] = []
    if len(bis) < 3:
        return segments

    cursor = 0
    while cursor + 2 < len(bis):
        if not _has_three_overlap(bis[cursor], bis[cursor + 1], bis[cursor + 2]):
            cursor += 1
            continue

        end_idx, _ = _find_segment_end(bis, cursor, require_case2_confirmation)
        if end_idx is None or end_idx <= cursor:
            tail = bis[-1]
            sl = bis[cursor:]
            segments.append(
                Segment(
                    direction=bis[cursor].direction,
                    start_index=sl[0].start_index,
                    end_index=tail.end_index,
                    high=max(item.high for item in sl),
                    low=min(item.low for item in sl),
                    event_time=tail.event_time,
                    available_time=max(item.available_time for item in sl),
                    status="provisional",
                )
            )
            break

        sl = bis[cursor : end_idx + 1]
        last = sl[-1]
        segments.append(
            Segment(
                direction=bis[cursor].direction,
                start_index=sl[0].start_index,
                end_index=last.end_index,
                high=max(item.high for item in sl),
                low=min(item.low for item in sl),
                event_time=last.event_time,
                available_time=max(item.available_time for item in sl),
                status="confirmed",
            )
        )
        cursor = end_idx + 1

    return segments
