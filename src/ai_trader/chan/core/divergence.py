from __future__ import annotations

from dataclasses import dataclass

from ai_trader.types import Bi, MACDPoint, TrendType


@dataclass(slots=True)
class DivergenceCandidate:
    signal_type: str
    mode: str
    confidence: float
    trigger: str
    invalid_if: str
    invalid_price: float
    event_time: object
    available_time: object


def _macd_area(macd: list[MACDPoint], start_time, end_time) -> float:
    points = [item for item in macd if start_time <= item.time <= end_time]
    if not points:
        return 0.0
    return sum(abs(item.hist) for item in points)


def _latest_pair_same_direction(bis: list[Bi], direction: str) -> tuple[Bi, Bi] | None:
    seq = [item for item in bis if item.direction == direction]
    if len(seq) < 2:
        return None
    return seq[-2], seq[-1]


def _strength(bi: Bi, macd: list[MACDPoint]) -> float:
    amp = abs(bi.end_price - bi.start_price)
    return amp + _macd_area(macd, bi.event_time, bi.available_time)


def _build_candidate(
    direction: str,
    mode: str,
    prev_bi: Bi,
    cur_bi: Bi,
    weaken_ratio: float,
) -> DivergenceCandidate:
    if direction == "down":
        signal_type = "B1"
        trigger = "主级别向下走势创新低但力度衰减（背驰候选）"
        invalid_if = f"价格继续跌破{cur_bi.end_price:.2f}并延续下行"
    else:
        signal_type = "S1"
        trigger = "主级别向上走势创新高但力度衰减（背驰候选）"
        invalid_if = f"价格继续突破{cur_bi.end_price:.2f}并延续上行"

    base = 0.60 if mode == "trend" else 0.50
    confidence = max(0.0, min(1.0, base + min(0.25, weaken_ratio)))

    return DivergenceCandidate(
        signal_type=signal_type,
        mode=mode,
        confidence=confidence,
        trigger=trigger,
        invalid_if=invalid_if,
        invalid_price=cur_bi.end_price,
        event_time=cur_bi.event_time,
        available_time=cur_bi.available_time,
    )


def detect_divergence_candidates(
    bis: list[Bi],
    zhongshu_count: int,
    trend_type: TrendType,
    macd: list[MACDPoint],
    threshold: float,
) -> list[DivergenceCandidate]:
    out: list[DivergenceCandidate] = []

    for direction in ("down", "up"):
        pair = _latest_pair_same_direction(bis, direction)
        if pair is None:
            continue

        prev_bi, cur_bi = pair
        if direction == "down":
            price_extreme = cur_bi.end_price < prev_bi.end_price
            trend_mode = trend_type == "down" and zhongshu_count >= 2
        else:
            price_extreme = cur_bi.end_price > prev_bi.end_price
            trend_mode = trend_type == "up" and zhongshu_count >= 2

        if not price_extreme:
            continue

        prev_strength = _strength(prev_bi, macd)
        cur_strength = _strength(cur_bi, macd)
        if prev_strength <= 0:
            continue

        weaken_ratio = (prev_strength - cur_strength) / prev_strength
        if weaken_ratio < threshold:
            continue

        mode = "trend" if trend_mode else "consolidation"
        out.append(_build_candidate(direction, mode, prev_bi, cur_bi, weaken_ratio))

    return out
