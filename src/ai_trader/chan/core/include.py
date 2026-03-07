from __future__ import annotations

from dataclasses import dataclass

from ai_trader.types import Bar


@dataclass(slots=True)
class MergeTrace:
    merged_index: int
    raw_indices: list[int]
    direction: int


def _has_inclusion(a: Bar, b: Bar) -> bool:
    return (a.high >= b.high and a.low <= b.low) or (a.high <= b.high and a.low >= b.low)


def _infer_direction(prev: Bar, cur: Bar) -> int:
    if cur.high > prev.high and cur.low > prev.low:
        return 1
    if cur.high < prev.high and cur.low < prev.low:
        return -1
    return 0


def _combine(prev: Bar, cur: Bar, direction: int) -> Bar:
    if direction >= 0:
        high = max(prev.high, cur.high)
        low = max(prev.low, cur.low)
    else:
        high = min(prev.high, cur.high)
        low = min(prev.low, cur.low)

    return Bar(
        time=cur.time,
        open=prev.open,
        high=high,
        low=low,
        close=cur.close,
        volume=prev.volume + cur.volume,
    )


def merge_inclusions_with_trace(bars: list[Bar]) -> tuple[list[Bar], list[MergeTrace]]:
    """按时间顺序逐根处理包含关系，并返回原始 K 线到合并结果的映射。"""
    if len(bars) < 2:
        merged = bars[:]
        traces = [
            MergeTrace(merged_index=idx, raw_indices=[idx], direction=0)
            for idx in range(len(merged))
        ]
        return merged, traces

    merged: list[Bar] = [bars[0]]
    raw_groups: list[list[int]] = [[0]]
    directions: list[int] = [0]
    direction = 0

    for raw_idx, cur in enumerate(bars[1:], start=1):
        prev = merged[-1]
        if not _has_inclusion(prev, cur):
            if len(merged) >= 1:
                direction = _infer_direction(prev, cur) or direction
            merged.append(cur)
            raw_groups.append([raw_idx])
            directions.append(direction)
            continue

        if direction == 0:
            direction = _infer_direction(prev, cur)
            if direction == 0:
                direction = 1

        merged[-1] = _combine(prev, cur, direction)
        raw_groups[-1].append(raw_idx)
        directions[-1] = direction

    traces = [
        MergeTrace(merged_index=idx, raw_indices=group, direction=directions[idx])
        for idx, group in enumerate(raw_groups)
    ]
    return merged, traces


def merge_inclusions(bars: list[Bar]) -> list[Bar]:
    """按时间顺序逐根处理包含关系。"""
    merged, _ = merge_inclusions_with_trace(bars)
    return merged
