from __future__ import annotations

from ai_trader.types import Bar


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


def merge_inclusions(bars: list[Bar]) -> list[Bar]:
    """按时间顺序逐根处理包含关系。"""
    if len(bars) < 2:
        return bars[:]

    merged: list[Bar] = [bars[0]]
    direction = 0

    for cur in bars[1:]:
        prev = merged[-1]
        if not _has_inclusion(prev, cur):
            if len(merged) >= 1:
                direction = _infer_direction(prev, cur) or direction
            merged.append(cur)
            continue

        if direction == 0:
            direction = _infer_direction(prev, cur)
            if direction == 0:
                direction = 1

        merged[-1] = _combine(prev, cur, direction)

    return merged
