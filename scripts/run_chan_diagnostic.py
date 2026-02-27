from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path, write_csv_rows

ensure_src_on_path()

from ai_trader.chan import build_chan_state, generate_signal
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.types import Bi, Fractal, Segment, Zhongshu, iso_utc, parse_utc_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Chan structure diagnostics on real BTC data")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe-main", default="4h")
    parser.add_argument("--timeframe-sub", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2026-02-10T00:00:00Z")
    parser.add_argument("--asof", default="", help="analysis timestamp in UTC ISO8601; default is --end")
    parser.add_argument("--output-root", default="outputs/diagnostics")
    return parser.parse_args()


def _fractal_row(item: Fractal) -> dict:
    return {
        "kind": item.kind,
        "index": item.index,
        "price": item.price,
        "event_time": iso_utc(item.event_time),
        "available_time": iso_utc(item.available_time),
        "status": item.status,
    }


def _bi_row(item: Bi) -> dict:
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


def _segment_row(item: Segment) -> dict:
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


def _zhongshu_row(item: Zhongshu) -> dict:
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


def _decision_block(decision_payload: dict) -> list[str]:
    lines = [
        "## Decision",
        "",
        f"- action: `{decision_payload['action']['decision']}`",
        f"- reason: {decision_payload['action']['reason']}",
        f"- risk: `{decision_payload['risk']['conflict_level']}` / {decision_payload['risk']['notes']}",
        f"- summary: {decision_payload['cn_summary']}",
        "",
        "## Signals",
        "",
    ]
    if not decision_payload["signals"]:
        lines.append("- none")
        return lines

    for sig in decision_payload["signals"]:
        lines.append(
            f"- {sig['type']} ({sig['level']}): conf={sig['confidence']:.2f}; trigger={sig['trigger']}; invalid={sig['invalid_if']}"
        )
    return lines


def main() -> None:
    args = parse_args()

    asof = parse_utc_time(args.asof if args.asof else args.end)

    bars_main = load_ohlcv(args.exchange, args.symbol, args.timeframe_main, args.start, args.end)
    bars_sub = load_ohlcv(args.exchange, args.symbol, args.timeframe_sub, args.start, args.end)

    snapshot = build_chan_state(
        bars_main=bars_main,
        bars_sub=bars_sub,
        macd_main=None,
        macd_sub=None,
        asof_time=asof,
        exchange=args.exchange,
        symbol=args.symbol,
        timeframe_main=args.timeframe_main,
        timeframe_sub=args.timeframe_sub,
    )
    decision = generate_signal(snapshot)
    payload = decision.to_contract_dict()

    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pair_key = args.symbol.replace("/", "")
    out_dir = Path(args.output_root) / f"{pair_key}_{args.timeframe_main}_{args.timeframe_sub}" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    snapshot_meta = {
        "exchange": args.exchange,
        "symbol": args.symbol,
        "timeframe_main": args.timeframe_main,
        "timeframe_sub": args.timeframe_sub,
        "start": args.start,
        "end": args.end,
        "asof": iso_utc(asof),
        "data_quality": payload["data_quality"],
        "market_state": payload["market_state"],
        "counts": {
            "bars_main": len(snapshot.bars_main),
            "bars_sub": len(snapshot.bars_sub),
            "fractals_main": len(snapshot.fractals_main),
            "fractals_sub": len(snapshot.fractals_sub),
            "bis_main": len(snapshot.bis_main),
            "bis_sub": len(snapshot.bis_sub),
            "segments_main": len(snapshot.segments_main),
            "segments_sub": len(snapshot.segments_sub),
            "zhongshus_main": len(snapshot.zhongshus_main),
            "zhongshus_sub": len(snapshot.zhongshus_sub),
        },
        "tail": {
            "fractals_main": [_fractal_row(x) for x in snapshot.fractals_main[-8:]],
            "bis_main": [_bi_row(x) for x in snapshot.bis_main[-8:]],
            "segments_main": [_segment_row(x) for x in snapshot.segments_main[-6:]],
            "zhongshus_main": [_zhongshu_row(x) for x in snapshot.zhongshus_main[-4:]],
            "fractals_sub": [_fractal_row(x) for x in snapshot.fractals_sub[-8:]],
            "bis_sub": [_bi_row(x) for x in snapshot.bis_sub[-8:]],
            "segments_sub": [_segment_row(x) for x in snapshot.segments_sub[-6:]],
            "zhongshus_sub": [_zhongshu_row(x) for x in snapshot.zhongshus_sub[-4:]],
        },
    }

    (out_dir / "snapshot_meta.json").write_text(json.dumps(snapshot_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "decision.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    write_csv_rows(out_dir / "fractals_main.csv", [_fractal_row(x) for x in snapshot.fractals_main])
    write_csv_rows(out_dir / "fractals_sub.csv", [_fractal_row(x) for x in snapshot.fractals_sub])
    write_csv_rows(out_dir / "bis_main.csv", [_bi_row(x) for x in snapshot.bis_main])
    write_csv_rows(out_dir / "bis_sub.csv", [_bi_row(x) for x in snapshot.bis_sub])
    write_csv_rows(out_dir / "segments_main.csv", [_segment_row(x) for x in snapshot.segments_main])
    write_csv_rows(out_dir / "segments_sub.csv", [_segment_row(x) for x in snapshot.segments_sub])
    write_csv_rows(out_dir / "zhongshus_main.csv", [_zhongshu_row(x) for x in snapshot.zhongshus_main])
    write_csv_rows(out_dir / "zhongshus_sub.csv", [_zhongshu_row(x) for x in snapshot.zhongshus_sub])

    summary_lines = [
        "# BTC 缠论结构诊断",
        "",
        f"- exchange: {args.exchange}",
        f"- symbol: {args.symbol}",
        f"- timeframe: {args.timeframe_main}/{args.timeframe_sub}",
        f"- asof: {iso_utc(asof)}",
        f"- bars(main/sub): {len(snapshot.bars_main)}/{len(snapshot.bars_sub)}",
        f"- data_quality: {payload['data_quality']['status']} ({payload['data_quality']['notes']})",
        "",
        "## Market State",
        "",
        f"- trend_type: {payload['market_state']['trend_type']}",
        f"- walk_type: {payload['market_state']['walk_type']}",
        f"- phase: {payload['market_state']['phase']}",
        f"- zhongshu_count: {payload['market_state']['zhongshu_count']}",
        f"- current_stroke_dir: {payload['market_state']['current_stroke_dir']}",
        f"- current_segment_dir: {payload['market_state']['current_segment_dir']}",
        f"- last_zhongshu: {payload['market_state']['last_zhongshu']}",
        "",
    ]
    summary_lines.extend(_decision_block(payload))

    (out_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"Diagnostic completed. Output: {out_dir}")


if __name__ == "__main__":
    main()
