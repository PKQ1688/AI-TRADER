from __future__ import annotations

from ai_trader.types import Bar, Fractal


def _is_top(left: Bar, mid: Bar, right: Bar, allow_equal: bool) -> bool:
    if allow_equal:
        return (
            mid.high >= left.high
            and mid.high >= right.high
            and mid.low >= left.low
            and mid.low >= right.low
            and (mid.high > left.high or mid.high > right.high or mid.low > left.low or mid.low > right.low)
        )
    return mid.high > left.high and mid.high > right.high and mid.low > left.low and mid.low > right.low


def _is_bottom(left: Bar, mid: Bar, right: Bar, allow_equal: bool) -> bool:
    if allow_equal:
        return (
            mid.low <= left.low
            and mid.low <= right.low
            and mid.high <= left.high
            and mid.high <= right.high
            and (mid.low < left.low or mid.low < right.low or mid.high < left.high or mid.high < right.high)
        )
    return mid.low < left.low and mid.low < right.low and mid.high < left.high and mid.high < right.high


def detect_fractals(bars: list[Bar], allow_equal: bool = False) -> list[Fractal]:
    out: list[Fractal] = []
    for i in range(1, len(bars) - 1):
        left, mid, right = bars[i - 1], bars[i], bars[i + 1]

        if _is_top(left, mid, right, allow_equal):
            out.append(
                Fractal(
                    kind="top",
                    index=i,
                    price=mid.high,
                    event_time=mid.time,
                    available_time=right.time,
                    status="confirmed",
                )
            )
            continue

        if _is_bottom(left, mid, right, allow_equal):
            out.append(
                Fractal(
                    kind="bottom",
                    index=i,
                    price=mid.low,
                    event_time=mid.time,
                    available_time=right.time,
                    status="confirmed",
                )
            )

    return out
