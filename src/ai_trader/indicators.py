from __future__ import annotations

from ai_trader.types import Bar, MACDPoint


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def compute_macd(bars: list[Bar], fast: int = 12, slow: int = 26, signal: int = 9) -> list[MACDPoint]:
    closes = [bar.close for bar in bars]
    if len(closes) < 2:
        return []

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    hist = [d - e for d, e in zip(dif, dea)]

    return [
        MACDPoint(time=bar.time, dif=d, dea=e, hist=h)
        for bar, d, e, h in zip(bars, dif, dea, hist)
    ]
