from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path, write_csv_rows

ensure_src_on_path()

from ai_trader.chan import build_chan_state, generate_signal
from ai_trader.chan.config import get_chan_config
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.indicators import compute_macd
from ai_trader.types import iso_utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay Chan structure and decisions bar-by-bar on real market data"
    )
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe-main", default="4h")
    parser.add_argument("--timeframe-sub", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2025-12-31T23:59:59Z")
    parser.add_argument("--chan-mode", default="strict_kline8", choices=("strict_kline8", "pragmatic"))
    parser.add_argument("--warmup-bars", type=int, default=120)
    parser.add_argument(
        "--tail-bars",
        type=int,
        default=40,
        help="number of recent focus rows to render in summary.md",
    )
    parser.add_argument("--output-root", default="outputs/replays")
    return parser.parse_args()


def _signal_types(signals: list[dict]) -> str:
    if not signals:
        return ""
    return ",".join(item["type"] for item in signals)


def _signal_summary(signals: list[dict]) -> str:
    if not signals:
        return ""
    return " | ".join(f"{item['type']}@{float(item['confidence']):.2f}" for item in signals)


def _last_zhongshu_fields(payload: dict) -> dict[str, float]:
    last = payload["market_state"]["last_zhongshu"]
    return {
        "last_zd": float(last["zd"]),
        "last_zg": float(last["zg"]),
        "last_gg": float(last["gg"]),
        "last_dd": float(last["dd"]),
    }


def _replay_row(snapshot, payload: dict, asof_close: float) -> dict:
    row = {
        "asof": iso_utc(snapshot.asof_time),
        "close": asof_close,
        "data_quality": payload["data_quality"]["status"],
        "action": payload["action"]["decision"],
        "reason": payload["action"]["reason"],
        "conflict_level": payload["risk"]["conflict_level"],
        "risk_notes": payload["risk"]["notes"],
        "trend_type": payload["market_state"]["trend_type"],
        "walk_type": payload["market_state"]["walk_type"],
        "phase": payload["market_state"]["phase"],
        "zhongshu_count": payload["market_state"]["zhongshu_count"],
        "current_stroke_dir": payload["market_state"]["current_stroke_dir"],
        "current_segment_dir": payload["market_state"]["current_segment_dir"],
        "raw_prev_main_time": (
            iso_utc(snapshot.previous_main_bar_time)
            if snapshot.previous_main_bar_time is not None
            else ""
        ),
        "bars_main": len(snapshot.bars_main),
        "bars_sub": len(snapshot.bars_sub),
        "bis_main": len(snapshot.bis_main),
        "bis_sub": len(snapshot.bis_sub),
        "segments_main": len(snapshot.segments_main),
        "segments_sub": len(snapshot.segments_sub),
        "signals": _signal_types(payload["signals"]),
        "signal_summary": _signal_summary(payload["signals"]),
        "signals_json": json.dumps(payload["signals"], ensure_ascii=False),
        "cn_summary": payload["cn_summary"],
    }
    row.update(_last_zhongshu_fields(payload))
    return row


def _render_counts(counter: Counter[str]) -> list[str]:
    if not counter:
        return ["- none"]
    return [f"- {key}: {value}" for key, value in sorted(counter.items())]


def _focus_markdown_rows(rows: list[dict]) -> list[str]:
    if not rows:
        return ["- none"]

    lines = [
        "| asof | close | phase | action | signals | summary |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['asof']} | {row['close']:.2f} | {row['phase']} | {row['action']} | "
            f"{row['signal_summary'] or '-'} | {row['cn_summary']} |"
        )
    return lines


def main() -> None:
    args = parse_args()
    cfg = get_chan_config(args.chan_mode)

    bars_main = load_ohlcv(
        args.exchange, args.symbol, args.timeframe_main, args.start, args.end
    )
    bars_sub = load_ohlcv(
        args.exchange, args.symbol, args.timeframe_sub, args.start, args.end
    )
    bars_main.sort(key=lambda x: x.time)
    bars_sub.sort(key=lambda x: x.time)

    if len(bars_main) <= args.warmup_bars:
        raise ValueError(
            f"bars_main={len(bars_main)} must be > warmup_bars={args.warmup_bars}"
        )

    macd_main_full = compute_macd(bars_main)
    macd_sub_full = compute_macd(bars_sub)

    rows: list[dict] = []
    focus_rows: list[dict] = []
    action_counter: Counter[str] = Counter()
    signal_counter: Counter[str] = Counter()
    phase_counter: Counter[str] = Counter()
    conflict_counter: Counter[str] = Counter()

    sub_cursor = 0
    for i in range(args.warmup_bars, len(bars_main)):
        bar = bars_main[i]
        while sub_cursor < len(bars_sub) and bars_sub[sub_cursor].time <= bar.time:
            sub_cursor += 1

        snapshot = build_chan_state(
            bars_main=bars_main[: i + 1],
            bars_sub=bars_sub[:sub_cursor],
            macd_main=macd_main_full,
            macd_sub=macd_sub_full,
            asof_time=bar.time,
            exchange=args.exchange,
            symbol=args.symbol,
            timeframe_main=args.timeframe_main,
            timeframe_sub=args.timeframe_sub,
            chan_config=cfg,
        )
        decision = generate_signal(snapshot=snapshot, chan_config=cfg)
        payload = decision.to_contract_dict()
        row = _replay_row(snapshot, payload, asof_close=bar.close)
        rows.append(row)

        action_counter[row["action"]] += 1
        phase_counter[row["phase"]] += 1
        conflict_counter[row["conflict_level"]] += 1
        for signal in payload["signals"]:
            signal_counter[signal["type"]] += 1

        if payload["signals"] or row["action"] not in {"hold", "wait"}:
            focus_rows.append(row)

    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pair_key = args.symbol.replace("/", "")
    out_dir = (
        Path(args.output_root)
        / f"{pair_key}_{args.timeframe_main}_{args.timeframe_sub}"
        / run_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    write_csv_rows(out_dir / "replay_rows.csv", rows)
    write_csv_rows(out_dir / "focus_rows.csv", focus_rows)

    summary = {
        "exchange": args.exchange,
        "symbol": args.symbol,
        "timeframe_main": args.timeframe_main,
        "timeframe_sub": args.timeframe_sub,
        "chan_mode": args.chan_mode,
        "period": {"start": args.start, "end": args.end},
        "warmup_bars": args.warmup_bars,
        "counts": {
            "rows": len(rows),
            "focus_rows": len(focus_rows),
            "actions": dict(action_counter),
            "signals": dict(signal_counter),
            "phases": dict(phase_counter),
            "conflicts": dict(conflict_counter),
        },
        "recent_focus_rows": focus_rows[-args.tail_bars :] if args.tail_bars > 0 else [],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary_lines = [
        "# 缠论逐 Bar 回放",
        "",
        f"- exchange: {args.exchange}",
        f"- symbol: {args.symbol}",
        f"- timeframe: {args.timeframe_main}/{args.timeframe_sub}",
        f"- chan_mode: {args.chan_mode}",
        f"- period: {args.start} -> {args.end}",
        f"- warmup_bars: {args.warmup_bars}",
        f"- replay_rows: {len(rows)}",
        f"- focus_rows: {len(focus_rows)}",
        "",
        "## Action Counts",
        "",
    ]
    summary_lines.extend(_render_counts(action_counter))
    summary_lines.extend(["", "## Signal Counts", ""])
    summary_lines.extend(_render_counts(signal_counter))
    summary_lines.extend(["", "## Phase Counts", ""])
    summary_lines.extend(_render_counts(phase_counter))
    summary_lines.extend(["", "## Conflict Counts", ""])
    summary_lines.extend(_render_counts(conflict_counter))
    summary_lines.extend(["", "## Recent Focus Rows", ""])
    tail_rows = focus_rows[-args.tail_bars :] if args.tail_bars > 0 else focus_rows
    summary_lines.extend(_focus_markdown_rows(tail_rows))

    (out_dir / "summary.md").write_text(
        "\n".join(summary_lines), encoding="utf-8"
    )
    print(f"Replay completed. Output: {out_dir}")


if __name__ == "__main__":
    main()
