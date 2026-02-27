from __future__ import annotations

from collections.abc import Sequence

from ai_trader.chan.config import ChanConfig, get_chan_config
from ai_trader.chan.core.buy_sell_points import build_risk, decide_action, generate_signals
from ai_trader.chan.core.center import build_zhongshus
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
    SignalDecision,
    parse_utc_time,
)


def _bars_until(bars: list[Bar], asof_time) -> list[Bar]:
    asof = parse_utc_time(asof_time)
    return [bar for bar in bars if bar.time <= asof]


def _normalize_macd(macd_values: Sequence[float] | Sequence[MACDPoint] | None, bars: list[Bar]) -> list[MACDPoint]:
    if macd_values is None:
        return compute_macd(bars)
    if not macd_values:
        return []

    first = macd_values[0]
    if isinstance(first, MACDPoint):
        return [item for item in macd_values if item.time <= bars[-1].time] if bars else list(macd_values)

    hist = [float(v) for v in macd_values]
    size = min(len(hist), len(bars))
    return [MACDPoint(time=bars[i].time, dif=0.0, dea=0.0, hist=hist[i]) for i in range(size)]


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
    cfg = chan_config or get_chan_config("strict_kline8")
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

    segments_main = build_segments(bis_main, require_case2_confirmation=cfg.require_case2_confirmation)
    segments_sub = build_segments(bis_sub, require_case2_confirmation=cfg.require_case2_confirmation)

    zhongshus_main = build_zhongshus(segments_main)
    zhongshus_sub = build_zhongshus(segments_sub)

    normalized_macd_main = _normalize_macd(macd_main, raw_main)
    normalized_macd_sub = _normalize_macd(macd_sub, raw_sub)

    last_close = merged_main[-1].close if merged_main else 0.0
    market_state = infer_market_state(last_close, bis_main, segments_main, zhongshus_main)

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


def generate_signal(
    snapshot: ChanSnapshot,
    macd_divergence_threshold: float = 0.10,
    min_confidence: float = 0.60,
    chan_config: ChanConfig | None = None,
) -> SignalDecision:
    cfg = chan_config or get_chan_config("strict_kline8")
    threshold = macd_divergence_threshold if macd_divergence_threshold > 0 else cfg.divergence_threshold
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
    )

    macd_missing = len(snapshot.macd_main) == 0
    bars_close = snapshot.bars_main[-1].close if snapshot.bars_main else 0.0
    signals = generate_signals(
        divergence_candidates=divergence,
        bis_main=snapshot.bis_main,
        bis_sub=snapshot.bis_sub,
        bars_close=bars_close,
        zhongshu_main=snapshot.last_zhongshu_main,
        market_state=market_state,
        macd_missing=macd_missing,
        missing_macd_penalty=cfg.missing_macd_penalty,
        transitional_confidence_cap=cfg.transitional_confidence_cap,
    )

    conflict_level, conflict_note = _conflict_level(snapshot)
    action, summary = decide_action(signals, market_state, conflict_level, confidence_floor, cfg)

    return SignalDecision(
        exchange=snapshot.exchange,
        symbol=snapshot.symbol,
        timeframe_main=snapshot.timeframe_main,
        timeframe_sub=snapshot.timeframe_sub,
        data_quality=snapshot.data_quality,
        market_state=market_state,
        signals=signals,
        action=action,
        risk=build_risk(conflict_level, conflict_note),
        cn_summary=summary,
    )
