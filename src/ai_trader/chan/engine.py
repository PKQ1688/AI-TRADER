from __future__ import annotations

from dataclasses import replace

from collections.abc import Sequence

from ai_trader.chan.config import ChanConfig, get_chan_config
from ai_trader.chan.core.buy_sell_points import (
    build_risk,
    decide_action,
    generate_signals,
)
from ai_trader.chan.core.center import build_zhongshus_from_bis
from ai_trader.chan.core.divergence import detect_divergence_candidates
from ai_trader.chan.core.fractal import detect_fractals
from ai_trader.chan.core.include import merge_inclusions
from ai_trader.chan.core.segment import build_segments
from ai_trader.chan.core.stroke import build_bis
from ai_trader.chan.core.trend_phase import infer_market_state
from ai_trader.indicators import compute_macd
from ai_trader.types import (
    Action,
    Bar,
    ChanSnapshot,
    DataQuality,
    MACDPoint,
    MarketState,
    Signal,
    SignalDecision,
    parse_utc_time,
)


def _bars_until(bars: list[Bar], asof_time) -> list[Bar]:
    asof = parse_utc_time(asof_time)
    return [bar for bar in bars if bar.time <= asof]


def _normalize_macd(
    macd_values: Sequence[float] | Sequence[MACDPoint] | None, bars: list[Bar]
) -> list[MACDPoint]:
    if macd_values is None:
        return compute_macd(bars)
    if not macd_values:
        return []

    first = macd_values[0]
    if isinstance(first, MACDPoint):
        return (
            [item for item in macd_values if item.time <= bars[-1].time]
            if bars
            else list(macd_values)
        )

    hist = [float(v) for v in macd_values]
    size = min(len(hist), len(bars))
    return [
        MACDPoint(time=bars[i].time, dif=0.0, dea=0.0, hist=hist[i])
        for i in range(size)
    ]


def _insufficient_snapshot(
    exchange: str,
    symbol: str,
    timeframe_main: str,
    timeframe_sub: str,
    asof_time,
    bars_main: list[Bar],
    bars_sub: list[Bar],
    notes: str,
) -> ChanSnapshot:
    return ChanSnapshot(
        exchange=exchange,
        symbol=symbol,
        timeframe_main=timeframe_main,
        timeframe_sub=timeframe_sub,
        asof_time=parse_utc_time(asof_time),
        bars_main=bars_main,
        bars_sub=bars_sub,
        macd_main=[],
        macd_sub=[],
        fractals_main=[],
        fractals_sub=[],
        bis_main=[],
        bis_sub=[],
        segments_main=[],
        segments_sub=[],
        previous_main_bar_time=bars_main[-2].time if len(bars_main) >= 2 else None,
        zhongshus_main=[],
        zhongshus_sub=[],
        last_zhongshu_main=None,
        trend_type_main="range",
        market_state_main=MarketState(trend_type="range"),
        data_quality=DataQuality(status="insufficient", notes=notes),
    )


def build_chan_state(
    bars_main: list[Bar],
    bars_sub: list[Bar],
    macd_main: Sequence[float] | Sequence[MACDPoint] | None,
    macd_sub: Sequence[float] | Sequence[MACDPoint] | None,
    asof_time,
    exchange: str = "binance",
    symbol: str = "BTC/USDT",
    timeframe_main: str = "4h",
    timeframe_sub: str = "1h",
    chan_config: ChanConfig | None = None,
) -> ChanSnapshot:
    cfg = chan_config or get_chan_config("orthodox_chan")
    asof = parse_utc_time(asof_time)

    raw_main = _bars_until(bars_main, asof)
    raw_sub = _bars_until(bars_sub, asof)

    if len(raw_main) < cfg.min_main_bars or len(raw_sub) < cfg.min_sub_bars:
        return _insufficient_snapshot(
            exchange=exchange,
            symbol=symbol,
            timeframe_main=timeframe_main,
            timeframe_sub=timeframe_sub,
            asof_time=asof,
            bars_main=raw_main,
            bars_sub=raw_sub,
            notes=(
                f"bars_main={len(raw_main)} (<{cfg.min_main_bars}) 或 "
                f"bars_sub={len(raw_sub)} (<{cfg.min_sub_bars})"
            ),
        )

    merged_main = merge_inclusions(raw_main)
    merged_sub = merge_inclusions(raw_sub)

    fractals_main = detect_fractals(merged_main, allow_equal=cfg.allow_equal_fractal)
    fractals_sub = detect_fractals(merged_sub, allow_equal=cfg.allow_equal_fractal)

    bis_main = build_bis(fractals_main, merged_main, min_bars=cfg.min_stroke_bars)
    bis_sub = build_bis(fractals_sub, merged_sub, min_bars=cfg.min_stroke_bars)

    segments_main = build_segments(
        bis_main, require_case2_confirmation=cfg.require_case2_confirmation
    )
    segments_sub = build_segments(
        bis_sub, require_case2_confirmation=cfg.require_case2_confirmation
    )

    zhongshus_main = build_zhongshus_from_bis(bis_main)
    zhongshus_sub = build_zhongshus_from_bis(bis_sub)

    normalized_macd_main = _normalize_macd(macd_main, raw_main)
    normalized_macd_sub = _normalize_macd(macd_sub, raw_sub)

    last_close = merged_main[-1].close if merged_main else 0.0
    market_state = infer_market_state(
        last_close, bis_main, segments_main, zhongshus_main
    )

    return ChanSnapshot(
        exchange=exchange,
        symbol=symbol,
        timeframe_main=timeframe_main,
        timeframe_sub=timeframe_sub,
        asof_time=asof,
        bars_main=merged_main,
        bars_sub=merged_sub,
        macd_main=normalized_macd_main,
        macd_sub=normalized_macd_sub,
        fractals_main=fractals_main,
        fractals_sub=fractals_sub,
        bis_main=bis_main,
        bis_sub=bis_sub,
        segments_main=segments_main,
        segments_sub=segments_sub,
        previous_main_bar_time=raw_main[-2].time if len(raw_main) >= 2 else None,
        zhongshus_main=zhongshus_main,
        zhongshus_sub=zhongshus_sub,
        last_zhongshu_main=zhongshus_main[-1] if zhongshus_main else None,
        trend_type_main=market_state.trend_type,
        market_state_main=market_state,
        data_quality=DataQuality(status="ok", notes=""),
    )


def _conflict_level(snapshot: ChanSnapshot) -> tuple[str, str]:
    if not snapshot.bis_sub:
        return "none", "次级别笔不足，按主级别执行"

    main = snapshot.trend_type_main
    sub_dir = snapshot.bis_sub[-1].direction

    if main == "up" and sub_dir == "up":
        return "none", "主次级别同向上行"
    if main == "down" and sub_dir == "down":
        return "none", "主次级别同向下行"
    if main == "range":
        return "low", "主级别盘整，次级别仅作辅助"
    return "high", "主次级别方向冲突"


def _signal_event_key(signal: Signal) -> tuple:
    if signal.type in {"B3", "S3"} and signal.anchor_center_start_index is not None:
        return (
            signal.type,
            signal.level,
            signal.anchor_center_start_index,
            signal.anchor_center_end_index,
        )
    return (
        signal.type,
        signal.level,
        signal.anchor_center_start_index,
        signal.anchor_center_end_index,
        signal.event_time,
    )


def _turning_signal_guard_key(signal: Signal) -> tuple | None:
    if signal.type not in {"B1", "B2", "S1", "S2"}:
        return None
    if signal.anchor_center_start_index is None:
        return None
    side = "buy" if signal.type.startswith("B") else "sell"
    return side, signal.anchor_center_start_index, signal.anchor_center_end_index


def _expire_turning_signal_guards(
    active_turning_guards: dict[tuple, dict[str, object]],
    asof_low: float | None,
    asof_high: float | None,
) -> None:
    expired: list[tuple] = []
    for key, state in active_turning_guards.items():
        invalid_price = state.get("invalid_price")
        if invalid_price is None:
            continue
        side = key[0]
        if side == "buy" and asof_low is not None and asof_low <= float(invalid_price):
            expired.append(key)
        elif side == "sell" and asof_high is not None and asof_high >= float(invalid_price):
            expired.append(key)

    for key in expired:
        active_turning_guards.pop(key, None)


def _keep_turning_signal(
    signal: Signal,
    active_turning_guards: dict[tuple, dict[str, object]],
) -> bool:
    key = _turning_signal_guard_key(signal)
    if key is None:
        return True

    state = active_turning_guards.get(key)
    if state is None:
        return True

    emitted_types = state.get("emitted_types", set())
    if signal.type in emitted_types:
        return False

    if signal.type in {"B1", "S1"}:
        return False

    return True


def _remember_turning_signal(
    signal: Signal,
    active_turning_guards: dict[tuple, dict[str, object]],
) -> None:
    key = _turning_signal_guard_key(signal)
    if key is None:
        return

    state = active_turning_guards.setdefault(
        key,
        {"invalid_price": signal.invalid_price, "emitted_types": set()},
    )
    emitted_types = state.get("emitted_types")
    if not isinstance(emitted_types, set):
        emitted_types = set(emitted_types or [])
        state["emitted_types"] = emitted_types
    emitted_types.add(signal.type)
    if signal.invalid_price is not None:
        state["invalid_price"] = signal.invalid_price


def suppress_seen_signal_events(
    decision: SignalDecision,
    seen_signal_keys: set[tuple],
    chan_config: ChanConfig,
    min_confidence: float,
    active_turning_guards: dict[tuple, dict[str, object]] | None = None,
    asof_low: float | None = None,
    asof_high: float | None = None,
) -> SignalDecision:
    if active_turning_guards is not None:
        _expire_turning_signal_guards(active_turning_guards, asof_low, asof_high)

    if not decision.signals:
        return decision

    kept = []
    new_keys = []
    turning_to_remember = []
    for item in decision.signals:
        if active_turning_guards is not None and not _keep_turning_signal(
            item, active_turning_guards
        ):
            continue
        key = _signal_event_key(item)
        if key in seen_signal_keys:
            continue
        kept.append(item)
        new_keys.append(key)
        if active_turning_guards is not None and _turning_signal_guard_key(item) is not None:
            turning_to_remember.append(item)

    for key in new_keys:
        seen_signal_keys.add(key)
    if active_turning_guards is not None:
        for item in turning_to_remember:
            _remember_turning_signal(item, active_turning_guards)

    if len(kept) == len(decision.signals):
        return decision

    action, summary = decide_action(
        kept,
        decision.market_state,
        decision.risk.conflict_level,
        min_confidence,
        chan_config,
    )
    return replace(decision, signals=kept, action=action, cn_summary=summary)


def _fresh_signals(snapshot: ChanSnapshot, signals):
    if not signals:
        return []
    prev_main_time = snapshot.previous_main_bar_time
    if prev_main_time is None and len(snapshot.bars_main) >= 2:
        prev_main_time = snapshot.bars_main[-2].time
    current_time = snapshot.asof_time
    return [
        item
        for item in signals
        if item.available_time <= current_time
        and (prev_main_time is None or item.available_time > prev_main_time)
    ]


def _invalidated_after_available(
    signal: Signal,
    bars: list[Bar],
    asof_time,
) -> bool:
    if signal.invalid_price is None:
        return False

    for bar in bars:
        if bar.time <= signal.available_time or bar.time > asof_time:
            continue
        if signal.type.startswith("B") and bar.low <= signal.invalid_price:
            return True
        if signal.type.startswith("S") and bar.high >= signal.invalid_price:
            return True

    return False


def _drop_invalidated_fresh_signals(
    snapshot: ChanSnapshot,
    signals: list[Signal],
) -> list[Signal]:
    if not signals:
        return []

    bars = snapshot.bars_sub if snapshot.bars_sub else snapshot.bars_main
    return [
        item
        for item in signals
        if not _invalidated_after_available(item, bars, snapshot.asof_time)
    ]


def _sub_interval_confirmed(
    snapshot: ChanSnapshot,
    main_candidates,
    threshold: float,
    cfg: ChanConfig,
):
    if not cfg.require_sub_interval_confirmation or not main_candidates or not snapshot.bars_sub:
        return list(main_candidates)

    sub_zhongshus = build_zhongshus_from_bis(snapshot.bis_sub)
    if not sub_zhongshus:
        return []

    sub_state = infer_market_state(
        snapshot.bars_sub[-1].close,
        snapshot.bis_sub,
        snapshot.segments_sub,
        sub_zhongshus,
    )
    sub_anchor_start = int(sub_state.oscillation_state.get("anchor_start_index", -1))
    sub_consolidation_anchor = next(
        (item for item in sub_zhongshus if item.start_index == sub_anchor_start),
        sub_zhongshus[-1],
    )
    sub_candidates = detect_divergence_candidates(
        bis=snapshot.bis_sub,
        zhongshu_count=sub_state.zhongshu_count,
        trend_type=sub_state.trend_type,
        macd=snapshot.macd_sub,
        threshold=threshold,
        zhongshus=sub_zhongshus,
        include_consolidation_divergence_hint=getattr(
            cfg, "include_consolidation_divergence_hint", False
        ),
        consolidation_anchor=sub_consolidation_anchor,
    )
    sub_signals = generate_signals(
        divergence_candidates=sub_candidates,
        bis_sub=snapshot.bis_sub,
        segments_sub=snapshot.segments_sub,
        zhongshus_sub=sub_zhongshus,
        zhongshu_main=sub_zhongshus[-1],
        market_state=sub_state,
        macd_missing=len(snapshot.macd_sub) == 0,
        missing_macd_penalty=cfg.missing_macd_penalty,
        transitional_confidence_cap=cfg.transitional_confidence_cap,
        bis_context=snapshot.bis_sub,
    )

    snapshot_anchor_time = (
        snapshot.last_zhongshu_main.available_time if snapshot.last_zhongshu_main else None
    )
    confirmed = []
    for candidate in main_candidates:
        if candidate.signal_type not in {"B1", "S1"}:
            confirmed.append(candidate)
            continue

        if candidate.signal_type == "B1":
            accepted_types = {"B2", "B3"} if candidate.mode == "consolidation" else {"B1", "B2", "B3"}
        else:
            accepted_types = {"S2", "S3"} if candidate.mode == "consolidation" else {"S1", "S2", "S3"}

        candidate_anchor_time = (
            candidate.anchor_center_available_time
            if getattr(candidate, "anchor_center_available_time", None) is not None
            else snapshot_anchor_time
        )
        lower_bound = max(
            item for item in (candidate_anchor_time, candidate.event_time) if item is not None
        )
        matched = any(
            item.type in accepted_types
            and lower_bound <= item.available_time <= snapshot.asof_time
            for item in sub_signals
        )
        if matched:
            confirmed.append(candidate)

    return confirmed


def _oscillation_note(market_state: MarketState) -> str:
    oscillation = market_state.oscillation_state
    if not oscillation or int(oscillation.get("count", 0)) <= 0:
        return ""

    breakout = str(oscillation.get("breakout", "none"))
    if breakout in {"above_zg", "below_zd"}:
        side = "上沿" if breakout == "above_zg" else "下沿"
        if bool(oscillation.get("first_breakout", False)):
            return f"Zn 首次越过中枢{side}，需区分第三类买卖点与中枢扩展"
        return f"Zn 仍处于越过中枢{side}后的震荡延续中"

    if bool(oscillation.get("limit_reached", False)):
        return "中枢震荡段数已超过 9，提防次级别升级"

    bias_map = {"strong": "偏强", "weak": "偏弱", "neutral": "中性"}
    bias = bias_map.get(str(oscillation.get("bias", "none")))
    if bias:
        return f"Zn 当前{bias}"

    return ""


def _consolidation_divergence_note(candidates) -> str:
    if any(getattr(item, "mode", None) == "consolidation" for item in candidates):
        return "检测到单中枢盘整背驰，仅作低级别买卖点确认背景，不直接视为本级别一类买卖点"
    return ""


def generate_signal(
    snapshot: ChanSnapshot,
    macd_divergence_threshold: float = 0.10,
    min_confidence: float = 0.60,
    chan_config: ChanConfig | None = None,
) -> SignalDecision:
    cfg = chan_config or get_chan_config("orthodox_chan")
    threshold = (
        macd_divergence_threshold
        if macd_divergence_threshold > 0
        else cfg.divergence_threshold
    )
    confidence_floor = min_confidence if min_confidence > 0 else cfg.min_confidence

    market_state = snapshot.market_state_main or MarketState(trend_type="range")
    if snapshot.data_quality.status != "ok":
        return SignalDecision(
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            timeframe_main=snapshot.timeframe_main,
            timeframe_sub=snapshot.timeframe_sub,
            data_quality=snapshot.data_quality,
            market_state=market_state,
            signals=[],
            action=Action(decision="wait", reason="数据不足，停止强判"),
            risk=build_risk("high", snapshot.data_quality.notes),
            cn_summary="当前数据不足，先补齐主次级别K线后再分析。",
        )

    divergence = detect_divergence_candidates(
        bis=snapshot.bis_main,
        zhongshu_count=market_state.zhongshu_count,
        trend_type=market_state.trend_type,
        macd=snapshot.macd_main,
        threshold=threshold,
        zhongshus=snapshot.zhongshus_main,
        include_consolidation_divergence_hint=cfg.include_consolidation_divergence_hint,
        consolidation_anchor=next(
            (
                item
                for item in snapshot.zhongshus_main
                if item.start_index
                == int(market_state.oscillation_state.get("anchor_start_index", -1))
            ),
            snapshot.last_zhongshu_main,
        ),
    )
    divergence = _sub_interval_confirmed(snapshot, divergence, threshold, cfg)

    macd_missing = len(snapshot.macd_main) == 0
    signals = generate_signals(
        divergence_candidates=divergence,
        bis_sub=snapshot.bis_sub,
        segments_sub=snapshot.segments_sub,
        zhongshus_sub=snapshot.zhongshus_sub,
        zhongshu_main=snapshot.last_zhongshu_main,
        market_state=market_state,
        macd_missing=macd_missing,
        missing_macd_penalty=cfg.missing_macd_penalty,
        transitional_confidence_cap=cfg.transitional_confidence_cap,
        bis_context=snapshot.bis_sub,
    )

    fresh_signals = _fresh_signals(snapshot, signals)
    fresh_signals = _drop_invalidated_fresh_signals(snapshot, fresh_signals)

    conflict_level, conflict_note = _conflict_level(snapshot)
    oscillation_note = _oscillation_note(market_state)
    consolidation_note = _consolidation_divergence_note(divergence)
    risk_parts = [conflict_note]
    if oscillation_note:
        risk_parts.append(oscillation_note)
    if consolidation_note:
        risk_parts.append(consolidation_note)
    risk_note = "；".join(risk_parts)
    action, summary = decide_action(
        fresh_signals, market_state, conflict_level, confidence_floor, cfg
    )

    return SignalDecision(
        exchange=snapshot.exchange,
        symbol=snapshot.symbol,
        timeframe_main=snapshot.timeframe_main,
        timeframe_sub=snapshot.timeframe_sub,
        data_quality=snapshot.data_quality,
        market_state=market_state,
        signals=fresh_signals,
        action=action,
        risk=build_risk(conflict_level, risk_note),
        cn_summary=summary,
    )
