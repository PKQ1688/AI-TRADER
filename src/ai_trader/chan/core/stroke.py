from __future__ import annotations

from ai_trader.types import Bar, Bi, Fractal


def _pick_extreme_same_kind(current: Fractal, incoming: Fractal) -> Fractal:
    if current.kind == "top":
        return incoming if incoming.price >= current.price else current
    return incoming if incoming.price <= current.price else current


def _valid_bi_pair(start: Fractal, end: Fractal, bars: list[Bar], min_bars: int) -> bool:
    if start.kind == end.kind:
        return False
    if end.index - start.index < 2:
        return False
    if end.index - start.index + 1 < min_bars:
        return False

    if start.kind == "bottom" and end.price <= start.price:
        return False
    if start.kind == "top" and end.price >= start.price:
        return False

    return True


def build_bis(fractals: list[Fractal], bars: list[Bar], min_bars: int = 5) -> list[Bi]:
    if len(fractals) < 2:
        return []

    bis: list[Bi] = []
    start = fractals[0]

    for fx in fractals[1:]:
        if fx.kind == start.kind:
            start = _pick_extreme_same_kind(start, fx)
            continue

        if _valid_bi_pair(start, fx, bars, min_bars):
            direction = "up" if start.kind == "bottom" else "down"
            bis.append(
                Bi(
                    direction=direction,
                    start_index=start.index,
                    end_index=fx.index,
                    start_price=start.price,
                    end_price=fx.price,
                    event_time=fx.event_time,
                    available_time=max(start.available_time, fx.available_time),
                    status="confirmed",
                )
            )
            start = fx

    return bis
