from __future__ import annotations

from typing import Iterable

from ai_trader.chan.config import ChanConfig
from ai_trader.chan.core.divergence import DivergenceCandidate
from ai_trader.types import Action, Bi, MarketState, Risk, Signal, Zhongshu


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _apply_phase_cap(signal_type: str, confidence: float, phase: str, cap: float) -> float:
    if phase != "transitional":
        return confidence
    if signal_type in {"B3", "S3"}:
        return confidence
    return min(confidence, cap)


def _derive_b2(b1: Signal, bis_sub: list[Bi]) -> Signal | None:
    if len(bis_sub) < 2:
        return None
    prev_bi, cur_bi = bis_sub[-2], bis_sub[-1]
    if not (prev_bi.direction == "down" and cur_bi.direction == "up"):
        return None

    invalid_price = b1.invalid_price
    if invalid_price is not None and prev_bi.low <= invalid_price:
        return None

    return Signal(
        type="B2",
        level="sub",
        trigger="一买后次级别回抽不破一买低点并重新转强",
        invalid_if=b1.invalid_if,
        confidence=_clamp01(b1.confidence + 0.12),
        event_time=cur_bi.event_time,
        available_time=cur_bi.available_time,
        invalid_price=b1.invalid_price,
    )


def _derive_s2(s1: Signal, bis_sub: list[Bi]) -> Signal | None:
    if len(bis_sub) < 2:
        return None
    prev_bi, cur_bi = bis_sub[-2], bis_sub[-1]
    if not (prev_bi.direction == "up" and cur_bi.direction == "down"):
        return None

    invalid_price = s1.invalid_price
    if invalid_price is not None and prev_bi.high >= invalid_price:
        return None

    return Signal(
        type="S2",
        level="sub",
        trigger="一卖后次级别反抽不破一卖高点并重新转弱",
        invalid_if=s1.invalid_if,
        confidence=_clamp01(s1.confidence + 0.12),
        event_time=cur_bi.event_time,
        available_time=cur_bi.available_time,
        invalid_price=s1.invalid_price,
    )


def _derive_b3(bars_close: float, bis_main: list[Bi], zhongshu: Zhongshu | None) -> Signal | None:
    if zhongshu is None or not bis_main:
        return None

    last_bi = bis_main[-1]
    if not (last_bi.direction == "up" and bars_close > zhongshu.zg):
        return None

    if len(bis_main) >= 2 and bis_main[-2].direction == "down" and bis_main[-2].low <= zhongshu.zg:
        return None

    return Signal(
        type="B3",
        level="main",
        trigger="向上离开中枢后首次回抽不回中枢并恢复上行",
        invalid_if=f"价格跌回中枢上沿{zhongshu.zg:.2f}下方",
        confidence=0.68,
        event_time=last_bi.event_time,
        available_time=last_bi.available_time,
        invalid_price=zhongshu.zg,
    )


def _derive_s3(bars_close: float, bis_main: list[Bi], zhongshu: Zhongshu | None) -> Signal | None:
    if zhongshu is None or not bis_main:
        return None

    last_bi = bis_main[-1]
    if not (last_bi.direction == "down" and bars_close < zhongshu.zd):
        return None

    if len(bis_main) >= 2 and bis_main[-2].direction == "up" and bis_main[-2].high >= zhongshu.zd:
        return None

    return Signal(
        type="S3",
        level="main",
        trigger="向下离开中枢后首次反抽不回中枢并继续下行",
        invalid_if=f"价格重新站回中枢下沿{zhongshu.zd:.2f}上方",
        confidence=0.68,
        event_time=last_bi.event_time,
        available_time=last_bi.available_time,
        invalid_price=zhongshu.zd,
    )


def generate_signals(
    divergence_candidates: list[DivergenceCandidate],
    bis_main: list[Bi],
    bis_sub: list[Bi],
    bars_close: float,
    zhongshu_main: Zhongshu | None,
    market_state: MarketState,
    macd_missing: bool,
    missing_macd_penalty: float,
    transitional_confidence_cap: float,
) -> list[Signal]:
    signals: list[Signal] = []

    for item in divergence_candidates:
        signals.append(
            Signal(
                type=item.signal_type,  # type: ignore[arg-type]
                level="main",
                trigger=item.trigger,
                invalid_if=item.invalid_if,
                confidence=item.confidence,
                event_time=item.event_time,
                available_time=item.available_time,
                invalid_price=item.invalid_price,
            )
        )

    b1 = next((x for x in signals if x.type == "B1"), None)
    s1 = next((x for x in signals if x.type == "S1"), None)

    if b1 is not None:
        b2 = _derive_b2(b1, bis_sub)
        if b2 is not None:
            signals.append(b2)
    if s1 is not None:
        s2 = _derive_s2(s1, bis_sub)
        if s2 is not None:
            signals.append(s2)

    b3 = _derive_b3(bars_close, bis_main, zhongshu_main)
    if b3 is not None:
        signals.append(b3)

    s3 = _derive_s3(bars_close, bis_main, zhongshu_main)
    if s3 is not None:
        signals.append(s3)

    for signal in signals:
        conf = signal.confidence
        if macd_missing:
            conf -= missing_macd_penalty
        conf = _apply_phase_cap(signal.type, conf, market_state.phase, transitional_confidence_cap)
        signal.confidence = _clamp01(conf)

    signals.sort(key=lambda x: x.confidence, reverse=True)
    return signals


def _best_signal(signals: Iterable[Signal], kinds: set[str], min_confidence: float) -> Signal | None:
    candidates = [x for x in signals if x.type in kinds and x.confidence >= min_confidence]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.confidence, reverse=True)
    return candidates[0]


def decide_action(
    signals: list[Signal],
    market_state: MarketState,
    conflict_level: str,
    min_confidence: float,
    chan_config: ChanConfig,
) -> tuple[Action, str]:
    buy_types = set(chan_config.execution_buy_types)
    reduce_types = set(chan_config.execution_reduce_types)
    sell_types = set(chan_config.execution_sell_types)

    buy_conf = max(min_confidence, chan_config.execution_buy_min_confidence)
    reduce_conf = max(min_confidence, chan_config.execution_reduce_min_confidence)

    best_buy = _best_signal(signals, buy_types, buy_conf)
    best_reduce = _best_signal(signals, reduce_types, reduce_conf)
    best_sell = _best_signal(signals, sell_types, reduce_conf) if sell_types else None
    best_b3 = _best_signal(signals, {"B3"}, min_confidence)
    best_s3 = _best_signal(signals, {"S3"}, min_confidence)
    has_turning_only = any(item.type in {"B1", "S1"} for item in signals)

    if market_state.phase == "transitional":
        if best_b3 is None and best_s3 is None:
            return Action(decision="wait", reason="中阴阶段默认等待新走势类型确认"), "处于中阴阶段，等待第三类买卖点确认后再操作。"

    if conflict_level == "high":
        if best_reduce is not None:
            return Action(decision="reduce", reason="主次级别冲突高，卖点仅用于降风险"), "主次级别冲突高，优先减仓而非激进反手。"
        return Action(decision="wait", reason="主次级别冲突高，放弃方向性动作"), "主次级别冲突高，等待结构统一后再执行。"

    if (
        best_buy is not None
        and (not chan_config.require_non_high_conflict_buy or conflict_level != "high")
        and (best_reduce is None or best_buy.confidence >= best_reduce.confidence)
    ):
        return Action(decision="buy", reason="出现保守确认买点（B2/B3）"), "当前出现保守确认买点，可按风控分批参与。"

    if best_sell is not None:
        return Action(decision="sell", reason="执行过滤后出现强卖出条件"), "出现强卖出条件，优先平仓控制风险。"

    if best_reduce is not None:
        if chan_config.reduce_only_on_high_conflict and conflict_level != "high":
            return Action(decision="hold", reason="减仓信号存在但冲突不高，按过滤规则继续持有"), "减仓信号已出现，但未达到高冲突条件，继续观察。"
        return Action(decision="reduce", reason="执行过滤后出现减仓信号"), "出现减仓信号，建议先降风险再观察主级别。"

    if has_turning_only:
        return Action(decision="hold", reason="仅出现一类买卖点候选，等待确认"), "当前仅为候选转折，维持观察或轻仓持有。"

    return Action(decision="hold", reason="未出现有效二三类买卖点"), "暂无明确执行信号，以观察为主。"


def build_risk(conflict_level: str, note: str) -> Risk:
    return Risk(conflict_level=conflict_level, notes=note)
