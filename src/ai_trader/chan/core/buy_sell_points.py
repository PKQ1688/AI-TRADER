from __future__ import annotations

from typing import Iterable

from ai_trader.chan.config import ChanConfig
from ai_trader.chan.core.divergence import DivergenceCandidate
from ai_trader.types import (
    Action,
    Bi,
    BuySellPoint,
    DivergenceState,
    MarketState,
    Risk,
    Segment,
    Signal,
    WalkState,
    Zhongshu,
)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _apply_phase_cap(
    signal_type: str, confidence: float, phase: str, cap: float
) -> float:
    if phase != "transitional":
        return confidence
    if signal_type in {"B3", "S3"}:
        return confidence
    return min(confidence, cap)


def allow_high_conflict_reversal(
    signal: Signal | None, market_state: MarketState
) -> bool:
    """Conservative execution never opens through high level conflict."""
    del signal, market_state
    return False


def _confirmed_bis_in_b2_window(
    bis_sub: list[Bi],
    anchor_time,
    cutoff_time,
) -> list[Bi]:
    return [
        item
        for item in bis_sub
        if item.status == "confirmed"
        and item.available_time > anchor_time
        and (cutoff_time is None or item.available_time < cutoff_time)
    ]


def _first_new_sub_center_after(
    zhongshus_sub: list[Zhongshu],
    anchor_time,
):
    times = [
        item.origin_available_time or item.available_time
        for item in zhongshus_sub
        if (item.origin_available_time or item.available_time) > anchor_time
    ]
    if not times:
        return None
    return min(times)


def _confirmed_segments(segments_sub: list[Segment]) -> list[Segment]:
    return [item for item in segments_sub if item.status == "confirmed"]


def _segment_overlaps_center(segment: Segment, zhongshu: Zhongshu) -> bool:
    return segment.high >= zhongshu.zd and segment.low <= zhongshu.zg


def _first_pullback_after_departure(
    segments_sub: list[Segment],
    zhongshu: Zhongshu,
    departure_direction: str,
) -> tuple[Segment, Segment] | None:
    center_confirmed_at = zhongshu.origin_available_time or zhongshu.available_time
    confirmed = [
        item
        for item in _confirmed_segments(segments_sub)
        if item.available_time > center_confirmed_at
    ]
    if len(confirmed) < 3:
        return None

    pullback_direction = "down" if departure_direction == "up" else "up"

    for pullback_idx in range(1, len(confirmed) - 1):
        pullback = confirmed[pullback_idx]
        if pullback.direction != pullback_direction:
            continue

        departure_end_idx = pullback_idx - 1
        departure_end = confirmed[departure_end_idx]
        if departure_end.direction != departure_direction:
            continue
        if departure_direction == "up" and departure_end.high <= zhongshu.zg:
            continue
        if departure_direction == "down" and departure_end.low >= zhongshu.zd:
            continue

        departure_start_idx = departure_end_idx
        while (
            departure_start_idx > 0
            and confirmed[departure_start_idx - 1].direction == departure_direction
        ):
            departure_start_idx -= 1

        overlap_idx = departure_start_idx - 1
        if overlap_idx < 0 or not _segment_overlaps_center(
            confirmed[overlap_idx], zhongshu
        ):
            continue

        confirm = confirmed[pullback_idx + 1]
        if confirm.direction != departure_direction:
            continue

        return pullback, confirm

    return None


def _first_bi_pullback_after_departure(
    bis_context: list[Bi],
    zhongshu: Zhongshu,
    departure_direction: str,
) -> tuple[Bi, Bi] | None:
    center_confirmed_at = zhongshu.origin_available_time or zhongshu.available_time
    confirmed = [
        item
        for item in bis_context
        if item.status == "confirmed" and item.available_time > center_confirmed_at
    ]
    if len(confirmed) < 3:
        return None

    pullback_direction = "down" if departure_direction == "up" else "up"
    saw_departure = False
    pullback = None

    for item in confirmed:
        if not saw_departure:
            if item.direction != departure_direction:
                continue
            if departure_direction == "up" and item.high <= zhongshu.zg:
                continue
            if departure_direction == "down" and item.low >= zhongshu.zd:
                continue
            saw_departure = True
            continue

        if pullback is None:
            if item.direction == departure_direction:
                continue
            if item.direction != pullback_direction:
                continue
            pullback = item
            continue

        if item.direction != departure_direction:
            continue
        return pullback, item

    return None


def _first_retrace_confirmation(
    bis_sub: list[Bi],
    anchor_time,
    departure_direction: str,
    retrace_direction: str,
    cutoff_time=None,
) -> tuple[Bi, Bi] | None:
    post = _confirmed_bis_in_b2_window(bis_sub, anchor_time, cutoff_time)
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


def _derive_b2(
    b1: Signal,
    bis_sub: list[Bi],
    zhongshus_sub: list[Zhongshu],
) -> Signal | None:
    cutoff_time = _first_new_sub_center_after(zhongshus_sub, b1.available_time)
    sequence = _first_retrace_confirmation(
        bis_sub=bis_sub,
        anchor_time=b1.available_time,
        departure_direction="up",
        retrace_direction="down",
        cutoff_time=cutoff_time,
    )
    if sequence is None:
        return None
    retrace_bi, confirm_bi = sequence

    invalid_price = b1.invalid_price
    if invalid_price is not None and retrace_bi.low < invalid_price:
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
        anchor_center_start_index=b1.anchor_center_start_index,
        anchor_center_end_index=b1.anchor_center_end_index,
        anchor_center_available_time=b1.anchor_center_available_time,
        source_level=b1.source_level,
        source="second_class",
        anchor_center_id=b1.anchor_center_id,
        divergence_mode=b1.divergence_mode,
        structure_path=list(b1.structure_path) + ["B2"],
    )


def _derive_s2(
    s1: Signal,
    bis_sub: list[Bi],
    zhongshus_sub: list[Zhongshu],
) -> Signal | None:
    cutoff_time = _first_new_sub_center_after(zhongshus_sub, s1.available_time)
    sequence = _first_retrace_confirmation(
        bis_sub=bis_sub,
        anchor_time=s1.available_time,
        departure_direction="down",
        retrace_direction="up",
        cutoff_time=cutoff_time,
    )
    if sequence is None:
        return None
    retrace_bi, confirm_bi = sequence

    invalid_price = s1.invalid_price
    if invalid_price is not None and retrace_bi.high > invalid_price:
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
        anchor_center_start_index=s1.anchor_center_start_index,
        anchor_center_end_index=s1.anchor_center_end_index,
        anchor_center_available_time=s1.anchor_center_available_time,
        source_level=s1.source_level,
        source="second_class",
        anchor_center_id=s1.anchor_center_id,
        divergence_mode=s1.divergence_mode,
        structure_path=list(s1.structure_path) + ["S2"],
    )


def _derive_b3(
    segments_sub: list[Segment],
    zhongshu: Zhongshu | None,
    bis_context: list[Bi] | None = None,
) -> Signal | None:
    if zhongshu is None:
        return None

    center_confirmed_at = zhongshu.origin_available_time or zhongshu.available_time

    if bis_context:
        sequence = _first_bi_pullback_after_departure(
            bis_context=bis_context,
            zhongshu=zhongshu,
            departure_direction="up",
        )
        if sequence is not None:
            pullback_bi, confirm_bi = sequence
            if pullback_bi.low >= zhongshu.zg and confirm_bi.low >= zhongshu.zg:
                available_time = max(confirm_bi.available_time, center_confirmed_at)
                return Signal(
                    type="B3",
                    level="main",
                    trigger="次级别向上离开中枢后，首次回抽未重新进入中枢",
                    invalid_if=f"价格跌回中枢上沿{zhongshu.zg:.2f}下方",
                    confidence=0.68,
                    event_time=pullback_bi.event_time,
                    available_time=available_time,
                    invalid_price=zhongshu.zg,
                    anchor_center_start_index=zhongshu.start_index,
                    anchor_center_end_index=zhongshu.end_index,
                    anchor_center_available_time=center_confirmed_at,
                    source_level=0,
                    source="third_class",
                    anchor_center_id=f"L0:zs:{zhongshu.start_index}-{zhongshu.end_index}",
                    structure_path=[
                        f"center:{zhongshu.start_index}-{zhongshu.end_index}",
                        "departure:up",
                        "first_pullback",
                    ],
                )

    sequence = _first_pullback_after_departure(
        segments_sub=segments_sub,
        zhongshu=zhongshu,
        departure_direction="up",
    )
    if sequence is None:
        return None
    pullback, confirm = sequence
    if pullback.low < zhongshu.zg or confirm.low < zhongshu.zg:
        return None

    available_time = max(confirm.available_time, center_confirmed_at)
    return Signal(
        type="B3",
        level="main",
        trigger="次级别向上离开中枢后，首次回抽未重新进入中枢",
        invalid_if=f"价格跌回中枢上沿{zhongshu.zg:.2f}下方",
        confidence=0.68,
        event_time=pullback.event_time,
        available_time=available_time,
        invalid_price=zhongshu.zg,
        anchor_center_start_index=zhongshu.start_index,
        anchor_center_end_index=zhongshu.end_index,
        anchor_center_available_time=center_confirmed_at,
        source_level=0,
        source="third_class",
        anchor_center_id=f"L0:zs:{zhongshu.start_index}-{zhongshu.end_index}",
        structure_path=[
            f"center:{zhongshu.start_index}-{zhongshu.end_index}",
            "departure:up",
            "first_pullback",
        ],
    )


def _derive_s3(
    segments_sub: list[Segment],
    zhongshu: Zhongshu | None,
    bis_context: list[Bi] | None = None,
) -> Signal | None:
    if zhongshu is None:
        return None

    center_confirmed_at = zhongshu.origin_available_time or zhongshu.available_time

    if bis_context:
        sequence = _first_bi_pullback_after_departure(
            bis_context=bis_context,
            zhongshu=zhongshu,
            departure_direction="down",
        )
        if sequence is not None:
            pullback_bi, confirm_bi = sequence
            if pullback_bi.high <= zhongshu.zd and confirm_bi.high <= zhongshu.zd:
                available_time = max(confirm_bi.available_time, center_confirmed_at)
                return Signal(
                    type="S3",
                    level="main",
                    trigger="次级别向下离开中枢后，首次反抽未重新进入中枢",
                    invalid_if=f"价格重新站回中枢下沿{zhongshu.zd:.2f}上方",
                    confidence=0.68,
                    event_time=pullback_bi.event_time,
                    available_time=available_time,
                    invalid_price=zhongshu.zd,
                    anchor_center_start_index=zhongshu.start_index,
                    anchor_center_end_index=zhongshu.end_index,
                    anchor_center_available_time=center_confirmed_at,
                    source_level=0,
                    source="third_class",
                    anchor_center_id=f"L0:zs:{zhongshu.start_index}-{zhongshu.end_index}",
                    structure_path=[
                        f"center:{zhongshu.start_index}-{zhongshu.end_index}",
                        "departure:down",
                        "first_pullback",
                    ],
                )

    sequence = _first_pullback_after_departure(
        segments_sub=segments_sub,
        zhongshu=zhongshu,
        departure_direction="down",
    )
    if sequence is None:
        return None
    pullback, confirm = sequence
    if pullback.high > zhongshu.zd or confirm.high > zhongshu.zd:
        return None

    available_time = max(confirm.available_time, center_confirmed_at)
    return Signal(
        type="S3",
        level="main",
        trigger="次级别向下离开中枢后，首次反抽未重新进入中枢",
        invalid_if=f"价格重新站回中枢下沿{zhongshu.zd:.2f}上方",
        confidence=0.68,
        event_time=pullback.event_time,
        available_time=available_time,
        invalid_price=zhongshu.zd,
        anchor_center_start_index=zhongshu.start_index,
        anchor_center_end_index=zhongshu.end_index,
        anchor_center_available_time=center_confirmed_at,
        source_level=0,
        source="third_class",
        anchor_center_id=f"L0:zs:{zhongshu.start_index}-{zhongshu.end_index}",
        structure_path=[
            f"center:{zhongshu.start_index}-{zhongshu.end_index}",
            "departure:down",
            "first_pullback",
        ],
    )


def _strict_second_class_from_walks(
    first_class: Signal,
    walks: list[WalkState],
) -> Signal | None:
    is_buy = first_class.type == "B1"
    departure_direction = "up" if is_buy else "down"
    retrace_direction = "down" if is_buy else "up"
    post = [
        item
        for item in walks
        if item.status == "confirmed"
        and item.available_time > first_class.available_time
    ]

    saw_departure = False
    for walk in post:
        if not saw_departure:
            if walk.direction == departure_direction:
                saw_departure = True
            continue
        if walk.direction == departure_direction:
            continue
        if walk.direction != retrace_direction:
            continue

        first_price = first_class.invalid_price
        weak = False
        if first_price is not None:
            weak = walk.low < first_price if is_buy else walk.high > first_price

        signal_type = "B2" if is_buy else "S2"
        weak_label = "弱二买" if is_buy else "弱二卖"
        normal_trigger = (
            "一买后首个已完成次级别回抽结束"
            if is_buy
            else "一卖后首个已完成次级别反抽结束"
        )
        trigger = (
            f"{normal_trigger}；{weak_label}越过一类点，仅记录不执行"
            if weak
            else normal_trigger
        )
        invalid_price = (
            walk.low
            if is_buy and weak
            else walk.high
            if (not is_buy and weak)
            else first_price
        )
        invalid_if = (
            f"价格继续跌破{invalid_price:.2f}"
            if is_buy and invalid_price is not None
            else f"价格继续突破{invalid_price:.2f}"
            if invalid_price is not None
            else first_class.invalid_if
        )
        return Signal(
            type=signal_type,  # type: ignore[arg-type]
            level="sub",
            trigger=trigger,
            invalid_if=invalid_if,
            confidence=1.0,
            event_time=walk.event_time,
            available_time=walk.available_time,
            invalid_price=invalid_price,
            anchor_center_start_index=first_class.anchor_center_start_index,
            anchor_center_end_index=first_class.anchor_center_end_index,
            anchor_center_available_time=(
                first_class.anchor_center_available_time
            ),
            source_level=first_class.source_level,
            source="second_class",
            anchor_center_id=first_class.anchor_center_id,
            divergence_mode=first_class.divergence_mode,
            structure_path=list(first_class.structure_path)
            + [f"{signal_type}:{'weak' if weak else 'standard'}"],
            executable=not weak,
        )
    return None


def _strict_third_class_from_walks(
    walks: list[WalkState],
    center: Zhongshu | None,
    *,
    direction: str,
    structure_level: int,
) -> Signal | None:
    if center is None:
        return None

    center_confirmed_at = center.origin_available_time or center.available_time
    post = [
        item
        for item in walks
        if item.status == "confirmed"
        and item.start_index >= center.end_index
        and item.available_time >= center_confirmed_at
    ]
    retrace_direction = "down" if direction == "up" else "up"
    departure_seen = False
    for walk in post:
        if not departure_seen:
            if walk.direction != direction:
                continue
            if direction == "up" and walk.high <= center.zg:
                continue
            if direction == "down" and walk.low >= center.zd:
                continue
            departure_seen = True
            continue

        if walk.direction == direction:
            continue
        if walk.direction != retrace_direction:
            continue

        boundary = center.zg if direction == "up" else center.zd
        outside = walk.low >= boundary if direction == "up" else walk.high <= boundary
        if not outside:
            # The definition requires the first completed return.  Once that
            # return re-enters the center, this departure cannot later be
            # relabeled as a third-class point.
            return None

        signal_type = "B3" if direction == "up" else "S3"
        return Signal(
            type=signal_type,  # type: ignore[arg-type]
            level="main",
            trigger=(
                "已完成次级别向上离开后，首次已完成回抽未跌破中枢上沿"
                if direction == "up"
                else "已完成次级别向下离开后，首次已完成反抽未突破中枢下沿"
            ),
            invalid_if=(
                f"价格跌回中枢上沿{boundary:.2f}下方"
                if direction == "up"
                else f"价格重新站回中枢下沿{boundary:.2f}上方"
            ),
            confidence=1.0,
            event_time=walk.event_time,
            available_time=max(walk.available_time, center_confirmed_at),
            invalid_price=boundary,
            anchor_center_start_index=center.start_index,
            anchor_center_end_index=center.end_index,
            anchor_center_available_time=center_confirmed_at,
            source_level=structure_level,
            source="third_class",
            anchor_center_id=(
                f"L{structure_level}:zs:{center.start_index}-{center.end_index}"
            ),
            structure_path=[
                f"center:{center.start_index}-{center.end_index}",
                f"completed_departure:{direction}",
                "completed_first_return_outside",
            ],
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
    zhongshus_sub: list[Zhongshu] | None = None,
    bis_context: list[Bi] | None = None,
    structural_walks: list[WalkState] | None = None,
    strict_mode: bool = False,
    structure_level: int = 0,
) -> list[Signal]:
    zhongshus_sub = zhongshus_sub or []
    signals: list[Signal] = []

    for item in divergence_candidates:
        if item.signal_type is None:
            continue
        signals.append(
            Signal(
                type=item.signal_type,
                level="main",
                trigger=item.trigger,
                invalid_if=item.invalid_if,
                confidence=1.0 if strict_mode else item.confidence,
                event_time=item.event_time,
                available_time=item.available_time,
                invalid_price=item.invalid_price,
                anchor_center_start_index=item.anchor_center_start_index,
                anchor_center_end_index=item.anchor_center_end_index,
                anchor_center_available_time=item.anchor_center_available_time,
                source_level=getattr(item, "level", 0),
                source="trend_divergence",
                anchor_center_id=getattr(item, "anchor_center_id", None),
                divergence_mode=getattr(item, "mode", None),
                structure_path=[
                    f"divergence:{getattr(item, 'mode', 'trend')}",
                    f"center:{item.anchor_center_start_index}-{item.anchor_center_end_index}",
                ],
            )
        )

    b1 = next((x for x in signals if x.type == "B1"), None)
    s1 = next((x for x in signals if x.type == "S1"), None)

    if b1 is not None:
        b2 = (
            _strict_second_class_from_walks(b1, structural_walks or [])
            if strict_mode
            else _derive_b2(b1, bis_sub, zhongshus_sub)
        )
        if b2 is not None:
            signals.append(b2)
    if s1 is not None:
        s2 = (
            _strict_second_class_from_walks(s1, structural_walks or [])
            if strict_mode
            else _derive_s2(s1, bis_sub, zhongshus_sub)
        )
        if s2 is not None:
            signals.append(s2)

    b3 = (
        _strict_third_class_from_walks(
            structural_walks or [],
            zhongshu_main,
            direction="up",
            structure_level=structure_level,
        )
        if strict_mode
        else _derive_b3(segments_sub, zhongshu_main, bis_context=bis_context)
    )
    if b3 is not None:
        signals.append(b3)

    s3 = (
        _strict_third_class_from_walks(
            structural_walks or [],
            zhongshu_main,
            direction="down",
            structure_level=structure_level,
        )
        if strict_mode
        else _derive_s3(segments_sub, zhongshu_main, bis_context=bis_context)
    )
    if s3 is not None:
        signals.append(s3)

    if not strict_mode:
        for signal in signals:
            conf = signal.confidence
            if macd_missing and signal.source == "trend_divergence":
                conf -= missing_macd_penalty
            conf = _apply_phase_cap(
                signal.type, conf, market_state.phase, transitional_confidence_cap
            )
            signal.confidence = _clamp01(conf)

    signals.sort(key=lambda x: x.confidence, reverse=True)
    return signals


def generate_buy_sell_points(
    signals: list[Signal],
    divergences: list[DivergenceState],
) -> list[BuySellPoint]:
    divergence_by_key = {
        (item.signal_type, item.anchor_center_start_index, item.anchor_center_end_index): item
        for item in divergences
        if item.signal_type is not None
    }
    points: list[BuySellPoint] = []
    for signal in signals:
        key = (
            signal.type,
            signal.anchor_center_start_index,
            signal.anchor_center_end_index,
        )
        divergence = divergence_by_key.get(key)
        source = signal.source
        if source not in {"trend_divergence", "second_class", "third_class"}:
            source = "policy"
        points.append(
            BuySellPoint(
                type=signal.type,
                level=signal.source_level,
                source=source,  # type: ignore[arg-type]
                signal=signal,
                center_id=signal.anchor_center_id,
                divergence=divergence,
            )
        )
    return points


def _best_signal(
    signals: Iterable[Signal], kinds: set[str], min_confidence: float
) -> Signal | None:
    candidates = [
        x
        for x in signals
        if x.executable
        and x.type in kinds
        and x.confidence >= min_confidence
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.confidence, reverse=True)
    return candidates[0]


def _preferred_signal(
    signals: Iterable[Signal], ordered_kinds: tuple[str, ...], min_confidence: float
) -> Signal | None:
    pool = list(signals)
    for kind in ordered_kinds:
        candidate = _best_signal(pool, {kind}, min_confidence)
        if candidate is not None:
            return candidate
    return None


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
    oscillation = market_state.oscillation_state
    oscillation_breakout = str(oscillation.get("breakout", "none"))
    first_breakout = bool(oscillation.get("first_breakout", False))
    limit_reached = bool(oscillation.get("limit_reached", False))

    if getattr(chan_config, "prefer_first_class_signals", False):
        preferred_buy = _preferred_signal(signals, ("B1", "B2", "B3"), buy_conf)
        preferred_reduce = _preferred_signal(signals, ("S1", "S2", "S3"), reduce_conf)
        preferred_sell = _preferred_signal(signals, ("S1", "S2", "S3"), reduce_conf)
        if preferred_buy is not None and preferred_buy.type in buy_types:
            best_buy = preferred_buy
        if preferred_reduce is not None and preferred_reduce.type in reduce_types:
            best_reduce = preferred_reduce
        if preferred_sell is not None and preferred_sell.type in sell_types:
            best_sell = preferred_sell

    allow_high_conflict_buy = allow_high_conflict_reversal(best_buy, market_state)
    allow_high_conflict_sell = allow_high_conflict_reversal(best_sell, market_state)

    strict_third_class_confirmation = (
        getattr(chan_config, "mode", None) in {"strict_recursive", "strict_kline8"}
        and (best_b3 is not None or best_s3 is not None)
    )
    if market_state.phase == "transitional" and not strict_third_class_confirmation:
        if best_reduce is not None:
            return (
                Action(decision="reduce", reason="中阴阶段卖点仅用于降风险"),
                "处于中阴阶段，卖点只用于降低风险，不作为反手开仓依据。",
            )
        if best_b3 is not None or best_s3 is not None:
            return (
                Action(decision="wait", reason="中阴阶段三类点先观察确认"),
                "处于中阴阶段，三类买卖点先降级观察，等待新走势类型确认。",
            )
        if best_b3 is None and best_s3 is None:
            if oscillation_breakout in {"above_zg", "below_zd"} and first_breakout:
                return (
                    Action(
                        decision="wait",
                        reason="中阴阶段内Zn首次越界，先区分三买卖与中枢扩展",
                    ),
                    "处于中阴阶段，Zn 已首次越界，需先区分第三类买卖点还是中枢扩展。",
                )
            if limit_reached:
                return (
                    Action(
                        decision="wait",
                        reason="中枢震荡段数已超过9，等待级别升级后的新结构",
                    ),
                    "当前中枢震荡已超过 9 段，优先等待次级别升级后的新结构确认。",
                )
            return Action(
                decision="wait", reason="中阴阶段默认等待新走势类型确认"
            ), "处于中阴阶段，等待第三类买卖点确认后再操作。"

    if (
        conflict_level == "high"
        and chan_config.require_non_high_conflict_buy
        and not allow_high_conflict_buy
        and not allow_high_conflict_sell
    ):
        if best_reduce is not None:
            return Action(
                decision="reduce", reason="主次级别冲突高，卖点仅用于降风险"
            ), "主次级别冲突高，优先减仓而非激进反手。"
        return Action(
            decision="wait", reason="主次级别冲突高，放弃方向性动作"
        ), "主次级别冲突高，等待结构统一后再执行。"

    if (
        best_buy is not None
        and chan_config.require_non_high_conflict_buy
        and market_state.phase != "trending"
        and not (
            getattr(chan_config, "mode", None) in {"strict_recursive", "strict_kline8"}
            and best_buy.type == "B3"
        )
    ):
        return Action(
            decision="wait", reason="买点出现但走势阶段未确认"
        ), "买点候选已出现，但当前走势阶段未确认，等待趋势状态明确后再执行。"

    if (
        best_buy is not None
        and (
            not chan_config.require_non_high_conflict_buy
            or conflict_level != "high"
            or allow_high_conflict_buy
        )
        and (best_reduce is None or best_buy.confidence >= best_reduce.confidence)
    ):
        if best_buy.type == "B1":
            return Action(
                decision="buy", reason="出现第一类买点（B1）"
            ), "当前出现一类买点，可按结构与风控执行。"
        return Action(
            decision="buy", reason="出现保守确认买点（B2/B3）"
        ), "当前出现保守确认买点，可按风控分批参与。"

    if best_sell is not None:
        if best_sell.type == "S1":
            return Action(
                decision="sell", reason="出现第一类卖点（S1）"
            ), "当前出现一类卖点，优先按结构退出或反手。"
        return Action(
            decision="sell", reason="执行过滤后出现强卖出条件"
        ), "出现强卖出条件，优先平仓控制风险。"

    if best_reduce is not None:
        if chan_config.reduce_only_on_high_conflict and conflict_level != "high":
            return Action(
                decision="hold", reason="减仓信号存在但冲突不高，按过滤规则继续持有"
            ), "减仓信号已出现，但未达到高冲突条件，继续观察。"
        return Action(
            decision="reduce", reason="执行过滤后出现减仓信号"
        ), "出现减仓信号，建议先降风险再观察主级别。"

    if has_turning_only:
        return Action(
            decision="hold", reason="仅出现一类买卖点候选，等待确认"
        ), "当前仅为候选转折，维持观察或轻仓持有。"

    return Action(
        decision="hold", reason="未出现有效二三类买卖点"
    ), "暂无明确执行信号，以观察为主。"


def build_risk(conflict_level: str, note: str) -> Risk:
    return Risk(conflict_level=conflict_level, notes=note)
