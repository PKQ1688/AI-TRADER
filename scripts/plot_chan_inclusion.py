from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path, write_csv_rows

ensure_src_on_path()

from ai_trader.chan.core.include import MergeTrace, merge_inclusions_with_trace
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.types import Bar, iso_utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot Chan K-line inclusion merge diagnostics"
    )
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2024-06-01T00:00:00Z")
    parser.add_argument("--window-size", type=int, default=80)
    parser.add_argument("--real-case-count", type=int, default=6)
    parser.add_argument("--skip-real", action="store_true")
    parser.add_argument("--output-root", default="outputs/chan_inclusion")
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
        "synthetic_up_include": [
            _bar(0, 8, 10, 5, 9),
            _bar(1, 9, 12, 6, 11),
            _bar(2, 11, 11, 7, 8),
            _bar(3, 8, 13, 8, 12),
            _bar(4, 12, 12, 9, 10),
            _bar(5, 10, 14, 10, 13),
        ],
        "synthetic_down_include": [
            _bar(0, 16, 20, 10, 12),
            _bar(1, 12, 18, 8, 9),
            _bar(2, 9, 17, 9, 15),
            _bar(3, 15, 16, 7, 8),
            _bar(4, 8, 15, 8, 13),
            _bar(5, 13, 14, 6, 7),
        ],
        "synthetic_chain_include": [
            _bar(0, 8, 10, 5, 9),
            _bar(1, 9, 12, 6, 11),
            _bar(2, 11, 11.8, 7, 10),
            _bar(3, 10, 11.5, 8, 11),
            _bar(4, 11, 11.4, 8.6, 10.5),
            _bar(5, 10.5, 13, 9, 12.5),
            _bar(6, 12.5, 12.6, 10, 11),
            _bar(7, 11, 14, 10.5, 13.5),
        ],
    }


def _direction_label(direction: int) -> str:
    if direction > 0:
        return "up"
    if direction < 0:
        return "down"
    return "unknown"


def _bar_row(idx: int, bar: Bar) -> dict[str, object]:
    return {
        "index": idx,
        "time": iso_utc(bar.time),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _trace_row(trace: MergeTrace, merged: Bar) -> dict[str, object]:
    return {
        "merged_index": trace.merged_index,
        "raw_indices": ",".join(str(x) for x in trace.raw_indices),
        "direction": _direction_label(trace.direction),
        "time": iso_utc(merged.time),
        "open": merged.open,
        "high": merged.high,
        "low": merged.low,
        "close": merged.close,
        "raw_count": len(trace.raw_indices),
    }


def _plot_candles(ax, bars: list[Bar], title: str, color_by_direction: bool) -> None:
    import matplotlib.patches as patches

    width = 0.58
    for idx, bar in enumerate(bars):
        if color_by_direction:
            color = "#2ca02c" if bar.close >= bar.open else "#d62728"
        else:
            color = "#777777"
        ax.vlines(idx, bar.low, bar.high, color=color, linewidth=1.2, alpha=0.95)
        body_low = min(bar.open, bar.close)
        body_height = max(abs(bar.close - bar.open), 0.02)
        rect = patches.Rectangle(
            (idx - width / 2, body_low),
            width,
            body_height,
            facecolor=color,
            edgecolor=color,
            alpha=0.55,
        )
        ax.add_patch(rect)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.25)
    ax.set_xlim(-0.8, max(len(bars) - 0.2, 0.8))


def plot_case(name: str, bars: list[Bar], out_dir: Path) -> dict[str, object]:
    import matplotlib.pyplot as plt

    merged, traces = merge_inclusions_with_trace(bars)

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(max(10, min(18, len(bars) * 0.35)), 7.5),
        constrained_layout=True,
        sharey=True,
    )

    _plot_candles(axes[0], bars, f"{name} - raw K-lines", color_by_direction=False)
    _plot_candles(axes[1], merged, f"{name} - merged K-lines", color_by_direction=True)

    for trace in traces:
        if len(trace.raw_indices) <= 1:
            continue
        start = min(trace.raw_indices) - 0.45
        end = max(trace.raw_indices) + 0.45
        axes[0].axvspan(start, end, color="#ffbf00", alpha=0.14)
        axes[0].text(
            (start + end) / 2,
            bars[max(trace.raw_indices)].high,
            f"M{trace.merged_index}\n{_direction_label(trace.direction)}",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#9a6700",
        )

    for trace, bar in zip(traces, merged, strict=True):
        if len(trace.raw_indices) <= 1:
            continue
        raw_label = (
            f"{trace.raw_indices[0]}-{trace.raw_indices[-1]}"
            if len(trace.raw_indices) > 1
            else str(trace.raw_indices[0])
        )
        axes[1].text(
            trace.merged_index,
            bar.high,
            f"raw {raw_label}\n{_direction_label(trace.direction)}",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#1f5f99",
        )

    for ax, series in zip(axes, [bars, merged], strict=True):
        step = max(1, len(series) // 8)
        ax.set_xticks(list(range(0, len(series), step)))
        ax.set_xlabel("bar index")
        ax.set_ylabel("price")

    png_path = out_dir / f"{name}.png"
    fig.savefig(png_path, dpi=150)
    plt.close(fig)

    raw_csv = out_dir / f"{name}_raw.csv"
    merged_csv = out_dir / f"{name}_merged.csv"
    write_csv_rows(raw_csv, [_bar_row(idx, bar) for idx, bar in enumerate(bars)])
    write_csv_rows(
        merged_csv,
        [_trace_row(trace, bar) for trace, bar in zip(traces, merged, strict=True)],
    )

    return {
        "name": name,
        "raw_count": len(bars),
        "merged_count": len(merged),
        "merged_groups": sum(1 for trace in traces if len(trace.raw_indices) > 1),
        "start_time": iso_utc(bars[0].time) if bars else "",
        "end_time": iso_utc(bars[-1].time) if bars else "",
        "chart": str(png_path),
        "raw_csv": str(raw_csv),
        "merged_csv": str(merged_csv),
    }


def _inclusion_score(window: list[Bar]) -> int:
    _, traces = merge_inclusions_with_trace(window)
    return sum(len(trace.raw_indices) - 1 for trace in traces)


def _best_real_windows(
    bars: list[Bar], window_size: int, case_count: int
) -> list[list[Bar]]:
    if len(bars) <= window_size:
        return [bars]

    step = max(1, window_size // 4)
    min_gap = max(1, window_size // 2)
    candidates: list[tuple[int, int]] = []
    for start in range(0, len(bars) - window_size + 1, step):
        window = bars[start : start + window_size]
        candidates.append((_inclusion_score(window), start))

    selected: list[int] = []
    for _, start in sorted(candidates, reverse=True):
        if all(abs(start - prev) >= min_gap for prev in selected):
            selected.append(start)
        if len(selected) >= max(1, case_count):
            break

    return [bars[start : start + window_size] for start in sorted(selected)]


def _try_load_real_cases(args: argparse.Namespace) -> tuple[list[list[Bar]], str | None]:
    if args.skip_real:
        return [], "Skipped real-data chart by --skip-real."
    try:
        bars = load_ohlcv(
            args.exchange,
            args.symbol,
            args.timeframe,
            args.start,
            args.end,
        )
    except Exception as exc:  # pragma: no cover - depends on network/cache
        return [], f"Real-data chart skipped: {type(exc).__name__}: {exc}"
    if not bars:
        return [], "Real-data chart skipped: no bars loaded."
    return _best_real_windows(bars, args.window_size, args.real_case_count), None


def main() -> None:
    args = parse_args()
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, object]] = []
    for name, bars in synthetic_cases().items():
        summaries.append(plot_case(name, bars, out_dir))

    real_cases, real_note = _try_load_real_cases(args)
    if real_cases:
        pair_key = args.symbol.replace("/", "")
        for case_idx, real_bars in enumerate(real_cases, start=1):
            summaries.append(
                plot_case(
                    f"real_{pair_key}_{args.timeframe}_case{case_idx:02d}",
                    real_bars,
                    out_dir,
                )
            )

    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "args": vars(args),
        "cases": summaries,
        "notes": [real_note] if real_note else [],
        "rule_summary": [
            "Process adjacent K-line inclusions strictly from left to right.",
            "Up inclusion keeps the higher high and the higher low.",
            "Down inclusion keeps the lower high and the lower low.",
            "After one merge, compare the merged K-line with the next raw K-line.",
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    args_json = json.dumps(vars(args), ensure_ascii=False)
    rows = []
    for item in summaries:
        row = dict(item)
        row["args"] = args_json
        rows.append(row)
    write_csv_rows(out_dir / "summary.csv", rows)

    lines = [
        "# Chan K-line Inclusion Diagnostics",
        "",
        "Rules:",
        "- Process adjacent inclusion relations from left to right.",
        "- Up inclusion: keep higher high and higher low.",
        "- Down inclusion: keep lower high and lower low.",
        "- A merged K-line is immediately compared with the next raw K-line.",
        "",
        "Cases:",
    ]
    for item in summaries:
        lines.append(
            f"- {item['name']}: raw={item['raw_count']}, merged={item['merged_count']}, "
            f"groups={item['merged_groups']}, time={item['start_time']} ~ {item['end_time']}, "
            f"chart={item['chart']}"
        )
    if real_note:
        lines.extend(["", f"Note: {real_note}"])
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Chan inclusion diagnostics completed. Output: {out_dir}")


if __name__ == "__main__":
    main()
