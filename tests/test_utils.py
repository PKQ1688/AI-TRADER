from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from ai_trader.types import Bar


def make_synthetic_bars(
    start: datetime,
    count: int,
    step_hours: int,
    start_price: float = 20000.0,
    drift: float = 5.0,
    wave_amp: float = 180.0,
) -> list[Bar]:
    bars: list[Bar] = []
    price = start_price
    for i in range(count):
        t = start + timedelta(hours=step_hours * i)
        wave = wave_amp * math.sin(i / 4.0)
        delta = drift + wave / 25.0
        open_price = price
        close_price = max(1.0, open_price + delta)
        high_price = max(open_price, close_price) + abs(wave) * 0.06 + 5
        low_price = min(open_price, close_price) - abs(wave) * 0.06 - 5
        bars.append(
            Bar(
                time=t.astimezone(timezone.utc),
                open=open_price,
                high=high_price,
                low=max(0.1, low_price),
                close=close_price,
                volume=100 + i,
            )
        )
        price = close_price
    return bars
