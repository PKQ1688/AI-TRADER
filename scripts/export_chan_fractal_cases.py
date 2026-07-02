from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path

ensure_src_on_path()

from ai_trader.chan.core.fractal import detect_fractals
from ai_trader.chan.core.include import MergeTrace, merge_inclusions_with_trace
from ai_trader.chan.core.stroke import build_bis
from ai_trader.chan.core.center import build_zhongshus_from_bis
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.types import Bar, Bi, Fractal, Zhongshu, iso_utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Chan inclusion and fractal cases for canvas review"
    )
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2024-06-01T00:00:00Z")
    parser.add_argument("--window-size", type=int, default=72)
    parser.add_argument("--case-count", type=int, default=8)
    parser.add_argument("--min-stroke-bars", type=int, default=5)
    parser.add_argument("--output-root", default="outputs/chan_fractal_cases")
    return parser.parse_args()


def _t(i: int) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * i)


def _bar(i: int, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        time=_t(i),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100 + i,
    )


def synthetic_cases() -> dict[str, list[Bar]]:
    return {
        "synthetic_include_top_bottom": [
            _bar(0, 10, 11, 8, 10.5),
            _bar(1, 10.5, 12, 9, 11.8),
            _bar(2, 11.8, 11.6, 9.8, 10.3),
            _bar(3, 10.3, 14, 11, 13.2),
            _bar(4, 13.2, 13, 10.8, 11.2),
            _bar(5, 11.2, 12.4, 9.2, 9.8),
            _bar(6, 9.8, 11.2, 8.1, 10.7),
            _bar(7, 10.7, 12.8, 9.4, 12.3),
            _bar(8, 12.3, 12.1, 10.1, 10.6),
            _bar(9, 10.6, 13.2, 10.8, 12.8),
        ],
        "synthetic_chain_then_fractal": [
            _bar(0, 8, 10, 5, 9),
            _bar(1, 9, 12, 6, 11),
            _bar(2, 11, 11.8, 7, 10),
            _bar(3, 10, 11.5, 8, 11),
            _bar(4, 11, 11.4, 8.6, 10.5),
            _bar(5, 10.5, 13, 9, 12.5),
            _bar(6, 12.5, 12.6, 10, 11),
            _bar(7, 11, 14, 10.5, 13.5),
            _bar(8, 13.5, 13.7, 11.2, 12),
            _bar(9, 12, 12.4, 9.7, 10.2),
            _bar(10, 10.2, 11.6, 8.4, 11),
        ],
    }


def _direction_label(direction: int) -> str:
    if direction > 0:
        return "up"
    if direction < 0:
        return "down"
    return "unknown"


def _bar_payload(index: int, bar: Bar) -> dict[str, object]:
    return {
        "index": index,
        "time": iso_utc(bar.time),
        "open": round(bar.open, 4),
        "high": round(bar.high, 4),
        "low": round(bar.low, 4),
        "close": round(bar.close, 4),
    }


def _trace_payload(trace: MergeTrace) -> dict[str, object]:
    return {
        "merged_index": trace.merged_index,
        "raw_indices": trace.raw_indices,
        "direction": _direction_label(trace.direction),
        "raw_count": len(trace.raw_indices),
    }


def _fractal_payload(
    fx: Fractal,
    merged: list[Bar],
    traces: list[MergeTrace],
) -> dict[str, object]:
    left = merged[fx.index - 1]
    mid = merged[fx.index]
    right = merged[fx.index + 1]
    is_top = (
        mid.high > left.high
        and mid.high > right.high
        and mid.low > left.low
        and mid.low > right.low
    )
    is_bottom = (
        mid.low < left.low
        and mid.low < right.low
        and mid.high < left.high
        and mid.high < right.high
    )
    return {
        "kind": fx.kind,
        "index": fx.index,
        "price": round(fx.price, 4),
        "event_time": iso_utc(fx.event_time),
        "available_time": iso_utc(fx.available_time),
        "raw_indices": traces[fx.index].raw_indices,
        "check": "pass" if (is_top if fx.kind == "top" else is_bottom) else "fail",
        "left_high": round(left.high, 4),
        "mid_high": round(mid.high, 4),
        "right_high": round(right.high, 4),
        "left_low": round(left.low, 4),
        "mid_low": round(mid.low, 4),
        "right_low": round(right.low, 4),
    }


def _find_fractal(fractals: list[Fractal], index: int, price: float) -> Fractal | None:
    for fx in fractals:
        if fx.index == index and abs(fx.price - price) < 1e-8:
            return fx
    return None


def _bi_payload(
    bi: Bi,
    merged: list[Bar],
    traces: list[MergeTrace],
    fractals: list[Fractal],
    min_bars: int,
) -> dict[str, object]:
    start_fx = _find_fractal(fractals, bi.start_index, bi.start_price)
    end_fx = _find_fractal(fractals, bi.end_index, bi.end_price)
    start_kind = start_fx.kind if start_fx else "unknown"
    end_kind = end_fx.kind if end_fx else "unknown"
    bars_count = bi.end_index - bi.start_index + 1
    direction_ok = (
        bi.direction == "up"
        and start_kind == "bottom"
        and end_kind == "top"
        and bi.end_price > bi.start_price
    ) or (
        bi.direction == "down"
        and start_kind == "top"
        and end_kind == "bottom"
        and bi.end_price < bi.start_price
    )
    min_bars_ok = bars_count >= min_bars
    alternate_ok = start_kind != end_kind and "unknown" not in {start_kind, end_kind}
    endpoint_ok = (
        start_fx is not None
        and end_fx is not None
        and merged[bi.start_index].low <= bi.start_price <= merged[bi.start_index].high
        and merged[bi.end_index].low <= bi.end_price <= merged[bi.end_index].high
    )
    raw_indices = [
        idx
        for trace in traces[bi.start_index : bi.end_index + 1]
        for idx in trace.raw_indices
    ]
    return {
        "direction": bi.direction,
        "start_index": bi.start_index,
        "end_index": bi.end_index,
        "start_kind": start_kind,
        "end_kind": end_kind,
        "start_price": round(bi.start_price, 4),
        "end_price": round(bi.end_price, 4),
        "bars_count": bars_count,
        "raw_indices": raw_indices,
        "event_time": iso_utc(bi.event_time),
        "available_time": iso_utc(bi.available_time),
        "check": "pass" if direction_ok and min_bars_ok and alternate_ok and endpoint_ok else "fail",
        "direction_ok": direction_ok,
        "min_bars_ok": min_bars_ok,
        "alternate_ok": alternate_ok,
        "endpoint_ok": endpoint_ok,
    }


def _zhongshu_payload(zs: Zhongshu, bis: list[Bi]) -> dict[str, object]:
    source_bi_indices = [
        idx
        for idx, bi in enumerate(bis)
        if bi.start_index >= zs.start_index and bi.end_index <= zs.end_index
    ]
    return {
        "zd": round(zs.zd, 4),
        "zg": round(zs.zg, 4),
        "gg": round(zs.gg, 4),
        "dd": round(zs.dd, 4),
        "g": round(zs.g, 4),
        "d": round(zs.d, 4),
        "start_index": zs.start_index,
        "end_index": zs.end_index,
        "event_time": iso_utc(zs.event_time),
        "available_time": iso_utc(zs.available_time),
        "evolution": zs.evolution,
        "status": zs.status,
        "source_bi_indices": source_bi_indices,
        "bi_count": len(source_bi_indices),
        "check": "pass" if zs.zd <= zs.zg and len(source_bi_indices) >= 3 else "fail",
    }


def _case_payload(name: str, bars: list[Bar], min_stroke_bars: int) -> dict[str, object]:
    merged, traces = merge_inclusions_with_trace(bars)
    fractals = detect_fractals(merged)
    bis = build_bis(fractals, merged, min_bars=min_stroke_bars)
    zhongshus = build_zhongshus_from_bis(bis)
    return {
        "name": name,
        "source": "synthetic" if name.startswith("synthetic") else "binance",
        "raw_count": len(bars),
        "merged_count": len(merged),
        "merged_groups": sum(1 for trace in traces if len(trace.raw_indices) > 1),
        "fractal_count": len(fractals),
        "top_count": sum(1 for fx in fractals if fx.kind == "top"),
        "bottom_count": sum(1 for fx in fractals if fx.kind == "bottom"),
        "bi_count": len(bis),
        "up_bi_count": sum(1 for bi in bis if bi.direction == "up"),
        "down_bi_count": sum(1 for bi in bis if bi.direction == "down"),
        "zhongshu_count": len(zhongshus),
        "start_time": iso_utc(bars[0].time) if bars else "",
        "end_time": iso_utc(bars[-1].time) if bars else "",
        "raw": [_bar_payload(idx, bar) for idx, bar in enumerate(bars)],
        "merged": [_bar_payload(idx, bar) for idx, bar in enumerate(merged)],
        "traces": [_trace_payload(trace) for trace in traces],
        "fractals": [_fractal_payload(fx, merged, traces) for fx in fractals],
        "bis": [
            _bi_payload(bi, merged, traces, fractals, min_bars=min_stroke_bars)
            for bi in bis
        ],
        "zhongshus": [_zhongshu_payload(zs, bis) for zs in zhongshus],
    }


def _window_score(window: list[Bar]) -> int:
    merged, traces = merge_inclusions_with_trace(window)
    fractals = detect_fractals(merged)
    bis = build_bis(fractals, merged, min_bars=5)
    zhongshus = build_zhongshus_from_bis(bis)
    merged_reduction = sum(len(trace.raw_indices) - 1 for trace in traces)
    return merged_reduction * 3 + len(fractals) * 2 + len(bis) * 4 + len(zhongshus) * 8


def _select_real_windows(
    bars: list[Bar],
    window_size: int,
    case_count: int,
) -> list[list[Bar]]:
    if len(bars) <= window_size:
        return [bars]

    step = max(1, window_size // 4)
    min_gap = max(1, window_size // 2)
    candidates = [
        (_window_score(bars[start : start + window_size]), start)
        for start in range(0, len(bars) - window_size + 1, step)
    ]
    selected: list[int] = []
    for _, start in sorted(candidates, reverse=True):
        if all(abs(start - prev) >= min_gap for prev in selected):
            selected.append(start)
        if len(selected) >= max(1, case_count):
            break
    return [bars[start : start + window_size] for start in sorted(selected)]


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_root) / datetime.now(tz=timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = [
        _case_payload(name, bars, min_stroke_bars=args.min_stroke_bars)
        for name, bars in synthetic_cases().items()
    ]

    bars = load_ohlcv(
        args.exchange,
        args.symbol,
        args.timeframe,
        args.start,
        args.end,
    )
    pair_key = args.symbol.replace("/", "")
    for idx, window in enumerate(
        _select_real_windows(bars, args.window_size, args.case_count),
        start=1,
    ):
        cases.append(
            _case_payload(
                f"{pair_key}_{args.timeframe}_case{idx:02d}",
                window,
                min_stroke_bars=args.min_stroke_bars,
            )
        )

    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "args": vars(args),
        "rule_summary": [
            "先按时间顺序处理相邻 K 线包含关系。",
            "向上包含取更高的高点和更高的低点；向下包含取更低的高点和更低的低点。",
            "顶分型：合并后三根相邻 K 线中，中间 K 的 high、low 均高于左右。",
            "底分型：合并后三根相邻 K 线中，中间 K 的 low、high 均低于左右。",
            "分型的 available_time 取右侧确认 K 线时间。",
            "笔：相邻有效顶底分型连接，方向由底到顶为向上笔、由顶到底为向下笔。",
            "当前实现要求一笔至少包含 min_stroke_bars 根合并后 K 线。",
            "中枢：至少三笔连续走势类型重叠形成；区间为 [ZD=max(低点), ZG=min(高点)]。",
            "后续笔与中枢区间重叠则视为中枢延伸，并更新 GG/DD/G/D 与结束位置。",
        ],
        "cases": cases,
    }
    output = out_dir / "chan_fractal_cases.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
