from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ai_trader.chan.config import ChanConfig
from ai_trader.chan.core.include import MergeTrace, merge_inclusions_with_trace
from ai_trader.chan.engine import build_chan_state, generate_signal
from ai_trader.types import (
    Bar,
    Bi,
    Fractal,
    MACDPoint,
    Segment,
    Signal,
    Zhongshu,
    iso_utc,
    parse_utc_time,
)


def _bars_until(bars: list[Bar], asof_time) -> list[Bar]:
    asof = parse_utc_time(asof_time)
    return [bar for bar in bars if bar.time <= asof]


def _bar_row(item: Bar, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "time": iso_utc(item.time),
        "open": item.open,
        "high": item.high,
        "low": item.low,
        "close": item.close,
        "volume": item.volume,
    }


def _merged_bar_row(item: Bar, trace: MergeTrace) -> dict[str, Any]:
    return {
        "index": trace.merged_index,
        "time": iso_utc(item.time),
        "open": item.open,
        "high": item.high,
        "low": item.low,
        "close": item.close,
        "volume": item.volume,
        "raw_indices": trace.raw_indices,
        "raw_start_index": trace.raw_indices[0],
        "raw_end_index": trace.raw_indices[-1],
        "merge_size": len(trace.raw_indices),
        "direction": (
            "up" if trace.direction > 0 else "down" if trace.direction < 0 else "flat"
        ),
    }


def _fractal_row(item: Fractal) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "index": item.index,
        "price": item.price,
        "event_time": iso_utc(item.event_time),
        "available_time": iso_utc(item.available_time),
        "status": item.status,
    }


def _bi_row(item: Bi) -> dict[str, Any]:
    return {
        "direction": item.direction,
        "start_index": item.start_index,
        "end_index": item.end_index,
        "start_price": item.start_price,
        "end_price": item.end_price,
        "high": item.high,
        "low": item.low,
        "event_time": iso_utc(item.event_time),
        "available_time": iso_utc(item.available_time),
        "status": item.status,
    }


def _segment_row(item: Segment) -> dict[str, Any]:
    return {
        "direction": item.direction,
        "start_index": item.start_index,
        "end_index": item.end_index,
        "high": item.high,
        "low": item.low,
        "event_time": iso_utc(item.event_time),
        "available_time": iso_utc(item.available_time),
        "status": item.status,
    }


def _zhongshu_row(item: Zhongshu) -> dict[str, Any]:
    return {
        "zd": item.zd,
        "zg": item.zg,
        "gg": item.gg,
        "dd": item.dd,
        "g": item.g,
        "d": item.d,
        "evolution": item.evolution,
        "start_index": item.start_index,
        "end_index": item.end_index,
        "event_time": iso_utc(item.event_time),
        "available_time": iso_utc(item.available_time),
        "status": item.status,
    }


def _signal_row(item: Signal) -> dict[str, Any]:
    return {
        "type": item.type,
        "level": item.level,
        "trigger": item.trigger,
        "invalid_if": item.invalid_if,
        "confidence": item.confidence,
        "event_time": iso_utc(item.event_time),
        "available_time": iso_utc(item.available_time),
        "invalid_price": item.invalid_price,
    }


def _slice_visible_merged(
    merged_rows: list[dict[str, Any]], raw_start_index: int
) -> list[dict[str, Any]]:
    if not merged_rows:
        return []

    start = 0
    for idx, item in enumerate(merged_rows):
        if item["raw_end_index"] >= raw_start_index:
            start = idx
            break
    return merged_rows[start:]


def _filter_indexed_items(
    rows: list[dict[str, Any]], key: str, index_start: int, index_end: int
) -> list[dict[str, Any]]:
    return [item for item in rows if index_start <= int(item[key]) <= index_end]


def _filter_range_items(
    rows: list[dict[str, Any]], start_key: str, end_key: str, index_start: int, index_end: int
) -> list[dict[str, Any]]:
    return [
        item
        for item in rows
        if int(item[end_key]) >= index_start and int(item[start_key]) <= index_end
    ]


def build_review_session_payload(
    bars_main: list[Bar],
    bars_sub: list[Bar],
    exchange: str,
    symbol: str,
    timeframe_main: str,
    timeframe_sub: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    return {
        "exchange": exchange,
        "symbol": symbol,
        "timeframe_main": timeframe_main,
        "timeframe_sub": timeframe_sub,
        "start": start,
        "end": end,
        "main_bar_count": len(bars_main),
        "sub_bar_count": len(bars_sub),
        "main_times": [iso_utc(bar.time) for bar in bars_main],
        "sub_times": [iso_utc(bar.time) for bar in bars_sub],
    }


def build_review_snapshot(
    bars_main: list[Bar],
    bars_sub: list[Bar],
    asof_time,
    exchange: str,
    symbol: str,
    timeframe_main: str,
    timeframe_sub: str,
    start: str,
    end: str,
    macd_main: Sequence[float] | Sequence[MACDPoint] | None = None,
    macd_sub: Sequence[float] | Sequence[MACDPoint] | None = None,
    window_main: int = 120,
    window_sub: int = 180,
    chan_config: ChanConfig | None = None,
) -> dict[str, Any]:
    asof = parse_utc_time(asof_time)
    raw_main = _bars_until(bars_main, asof)
    raw_sub = _bars_until(bars_sub, asof)

    merged_main, trace_main = merge_inclusions_with_trace(raw_main)
    merged_sub, trace_sub = merge_inclusions_with_trace(raw_sub)

    snapshot = build_chan_state(
        bars_main=raw_main,
        bars_sub=raw_sub,
        macd_main=macd_main,
        macd_sub=macd_sub,
        asof_time=asof,
        exchange=exchange,
        symbol=symbol,
        timeframe_main=timeframe_main,
        timeframe_sub=timeframe_sub,
        chan_config=chan_config,
    )
    decision = generate_signal(snapshot=snapshot, chan_config=chan_config)

    raw_main_rows = [_bar_row(item, idx) for idx, item in enumerate(raw_main)]
    raw_sub_rows = [_bar_row(item, idx) for idx, item in enumerate(raw_sub)]
    merged_main_rows = [
        _merged_bar_row(item, trace) for item, trace in zip(merged_main, trace_main, strict=False)
    ]
    merged_sub_rows = [
        _merged_bar_row(item, trace) for item, trace in zip(merged_sub, trace_sub, strict=False)
    ]

    raw_main_start = max(0, len(raw_main_rows) - max(window_main, 1))
    raw_sub_start = max(0, len(raw_sub_rows) - max(window_sub, 1))

    raw_main_visible = raw_main_rows[raw_main_start:]
    raw_sub_visible = raw_sub_rows[raw_sub_start:]
    merged_main_visible = _slice_visible_merged(merged_main_rows, raw_main_start)
    merged_sub_visible = _slice_visible_merged(merged_sub_rows, raw_sub_start)

    main_index_start = (
        int(merged_main_visible[0]["index"]) if merged_main_visible else 0
    )
    main_index_end = (
        int(merged_main_visible[-1]["index"]) if merged_main_visible else -1
    )
    sub_index_start = int(merged_sub_visible[0]["index"]) if merged_sub_visible else 0
    sub_index_end = int(merged_sub_visible[-1]["index"]) if merged_sub_visible else -1

    fractals_main = [_fractal_row(item) for item in snapshot.fractals_main]
    fractals_sub = [_fractal_row(item) for item in snapshot.fractals_sub]
    bis_main = [_bi_row(item) for item in snapshot.bis_main]
    bis_sub = [_bi_row(item) for item in snapshot.bis_sub]
    segments_main = [_segment_row(item) for item in snapshot.segments_main]
    segments_sub = [_segment_row(item) for item in snapshot.segments_sub]
    zhongshus_main = [_zhongshu_row(item) for item in snapshot.zhongshus_main]
    zhongshus_sub = [_zhongshu_row(item) for item in snapshot.zhongshus_sub]
    signals = [_signal_row(item) for item in decision.signals]

    return {
        "meta": {
            "exchange": exchange,
            "symbol": symbol,
            "timeframe_main": timeframe_main,
            "timeframe_sub": timeframe_sub,
            "start": start,
            "end": end,
            "asof": iso_utc(asof),
            "window_main": max(window_main, 1),
            "window_sub": max(window_sub, 1),
            "previous_main_bar_time": (
                iso_utc(snapshot.previous_main_bar_time)
                if snapshot.previous_main_bar_time is not None
                else None
            ),
        },
        "summary": {
            "raw_main_count": len(raw_main_rows),
            "raw_sub_count": len(raw_sub_rows),
            "merged_main_count": len(merged_main_rows),
            "merged_sub_count": len(merged_sub_rows),
            "visible_raw_main_count": len(raw_main_visible),
            "visible_raw_sub_count": len(raw_sub_visible),
            "visible_merged_main_count": len(merged_main_visible),
            "visible_merged_sub_count": len(merged_sub_visible),
        },
        "decision": decision.to_contract_dict(),
        "signals_full": signals,
        "main": {
            "raw_bars": raw_main_visible,
            "merged_bars": merged_main_visible,
            "fractals": _filter_indexed_items(
                fractals_main, "index", main_index_start, main_index_end
            ),
            "bis": _filter_range_items(
                bis_main, "start_index", "end_index", main_index_start, main_index_end
            ),
            "segments": _filter_range_items(
                segments_main,
                "start_index",
                "end_index",
                main_index_start,
                main_index_end,
            ),
            "zhongshus": _filter_range_items(
                zhongshus_main,
                "start_index",
                "end_index",
                main_index_start,
                main_index_end,
            ),
        },
        "sub": {
            "raw_bars": raw_sub_visible,
            "merged_bars": merged_sub_visible,
            "fractals": _filter_indexed_items(
                fractals_sub, "index", sub_index_start, sub_index_end
            ),
            "bis": _filter_range_items(
                bis_sub, "start_index", "end_index", sub_index_start, sub_index_end
            ),
            "segments": _filter_range_items(
                segments_sub,
                "start_index",
                "end_index",
                sub_index_start,
                sub_index_end,
            ),
            "zhongshus": _filter_range_items(
                zhongshus_sub,
                "start_index",
                "end_index",
                sub_index_start,
                sub_index_end,
            ),
        },
    }
