from __future__ import annotations

from typing import Iterable

from ai_trader.chan.config import ChanConfig
from ai_trader.chan.core.divergence import DivergenceCandidate
from ai_trader.types import Action, Bi, MarketState, Risk, Segment, Signal, Zhongshu


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _apply_phase_cap(signal_type: str, confidence: float, phase: str, cap: float) -> float:
    if phase != "transitional":
        return confidence
    if signal_type in {"B3", "S3"}:
        return confidence
    return min(confidence, cap)


def _confirmed_bis_after(bis_sub: list[Bi], anchor_time) -> list[Bi]:
    return [item for item in bis_sub if item.status == "confirmed" and item.available_time > anchor_time]


def _confirmed_segments(segments_sub: list[Segment]) -> list[Segment]:
    return [item for item in segments_sub if item.status == "confirmed"]


def _segment_overlaps_center(segment: Segment, zhongshu: Zhongshu) -> bool:
    return segment.high >= zhongshu.zd and segment.low <= zhongshu.zg


def _first_retrace_confirmation(
    bis_sub: list[Bi],
    anchor_time,
    departure_direction: str,
    retrace_direction: str,
) -> tuple[Bi, Bi] | None:
    post = _confirmed_bis_after(bis_sub, anchor_time)
    if len(post) < 2:
        return None

    saw_departure = False
    for idx, item in enumerate(post):
        if item.direction == departure_direction:
            saw_departure = True
            continue
        if not saw_departure or item.direction != retrace_direction:
            continue
        if idx + 1 >= len(post):
            return None
        confirm = post[idx + 1]
        if confirm.direction != departure_direction:
            return None
        return item, confirm

    return None


def _derive_b2(b1: Signal, bis_sub: list[Bi]) -> Signal | None:
    sequence = _first_retrace_confirmation(
        bis_sub=bis_sub,
        anchor_time=b1.available_time,
        departure_direction="up",
        retrace_direction="down",
    )
    if sequence is None:
        return None
    retrace_bi, confirm_bi = sequence

    invalid_price = b1.invalid_price
    if invalid_price is not None and retrace_bi.low <= invalid_price:
        return None

    return Signal(
        type="B2",
        level="sub",
        trigger="一买后次级别首次回抽不破一买低点，并再次转强",
        invalid_if=b1.invalid_if,
        confidence=_clamp01(b1.confidence + 0.12),
        event_time=confirm_bi.event_time,
        available_time=confirm_bi.available_time,
        invalid_price=b1.invalid_price,
    )


def _derive_s2(s1: Signal, bis_sub: list[Bi]) -> Signal | None:
    sequence = _first_retrace_confirmation(
        bis_sub=bis_sub,
        anchor_time=s1.available_time,
        departure_direction="down",
        retrace_direction="up",
    )
    if sequence is None:
        return None
    retrace_bi, confirm_bi = sequence

    invalid_price = s1.invalid_price
    if invalid_price is not None and retrace_bi.high >= invalid_price:
        return None

    return Signal(
        type="S2",
        level="sub",
        trigger="一卖后次级别首次反抽不破一卖高点，并再次转弱",
        invalid_if=s1.invalid_if,
        confidence=_clamp01(s1.confidence + 0.12),
        event_time=confirm_bi.event_time,
        available_time=confirm_bi.available_time,
        invalid_price=s1.invalid_price,
    )


def _derive_b3(segments_sub: list[Segment], zhongshu: Zhongshu | None) -> Signal | None:
    if zhongshu is None:
        return None

    confirmed = _confirmed_segments(segments_sub)
    if len(confirmed) < 2:
        return None

    for pullback_idx in range(len(confirmed) - 1, 0, -1):
        pullback = confirmed[pullback_idx]
        if pullback.direction != "down" or pullback.low <= zhongshu.zg:
            continue

        departure_end_idx = pullback_idx - 1
        departure_end = confirmed[departure_end_idx]
        if departure_end.direction != "up" or departure_end.high <= zhongshu.zg:
            continue

        departure_start_idx = departure_end_idx
        while departure_start_idx > 0 and confirmed[departure_start_idx - 1].direction == "up":
            departure_start_idx -= 1

        overlap_idx = departure_start_idx - 1
        if overlap_idx < 0 or not _segment_overlaps_center(confirmed[overlap_idx], zhongshu):
            continue

        available_time = max(pullback.available_time, zhongshu.available_time)
        return Signal(
            type="B3",
            level="main",
            trigger="次级别向上离开中枢后，首次回抽未重新进入中枢",
            invalid_if=f"价格跌回中枢上沿{zhongshu.zg:.2f}下方",
            confidence=0.68,
            event_time=pullback.event_time,
            available_time=available_time,
            invalid_price=zhongshu.zg,
        )

    return None


def _derive_s3(segments_sub: list[Segment], zhongshu: Zhongshu | None) -> Signal | None:
    if zhongshu is None:
        return None

    confirmed = _confirmed_segments(segments_sub)
    if len(confirmed) < 2:
        return None

    for pullback_idx in range(len(confirmed) - 1, 0, -1):
        pullback = confirmed[pullback_idx]
        if pullback.direction != "up" or pullback.high >= zhongshu.zd:
            continue

        departure_end_idx = pullback_idx - 1
        departure_end = confirmed[departure_end_idx]
        if departure_end.direction != "down" or departure_end.low >= zhongshu.zd:
            continue

        departure_start_idx = departure_end_idx
        while departure_start_idx > 0 and confirmed[departure_start_idx - 1].direction == "down":
            departure_start_idx -= 1

        overlap_idx = departure_start_idx - 1
        if overlap_idx < 0 or not _segment_overlaps_center(confirmed[overlap_idx], zhongshu):
            continue

        available_time = max(pullback.available_time, zhongshu.available_time)
        return Signal(
            type="S3",
            level="main",
            trigger="次级别向下离开中枢后，首次反抽未重新进入中枢",
            invalid_if=f"价格重新站回中枢下沿{zhongshu.zd:.2f}上方",
            confidence=0.68,
            event_time=pullback.event_time,
            available_time=available_time,
            invalid_price=zhongshu.zd,
        )

    return None


def generate_signals(
    divergence_candidates: list[DivergenceCandidate],
    bis_sub: list[Bi],
    segments_sub: list[Segment],
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

    b3 = _derive_b3(segments_sub, zhongshu_main)
    if b3 is not None:
        signals.append(b3)

    s3 = _derive_s3(segments_sub, zhongshu_main)
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
