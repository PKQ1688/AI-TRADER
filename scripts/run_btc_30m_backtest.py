from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path

ensure_src_on_path()

from audit_ohlcv_cache import audit
from ai_trader.backtest import run_backtest
from ai_trader.data import load_funding_rates, load_ohlcv
from ai_trader.types import BacktestConfig, iso_utc, parse_utc_time


def _availability_start(raw_start: str, minutes: int) -> str:
    return iso_utc(parse_utc_time(raw_start) + timedelta(minutes=minutes))


def _load_market_data(
    exchange: str,
    raw_start: str,
    end_close: str,
):
    common = {"exchange": exchange, "symbol": "BTC/USDT"}
    bars_execution = load_ohlcv(
        **common,
        timeframe="1m",
        start_utc=_availability_start(raw_start, 1),
        end_utc=end_close,
    )
    bars_sub = load_ohlcv(
        **common,
        timeframe="5m",
        start_utc=_availability_start(raw_start, 5),
        end_utc=end_close,
    )
    bars_main = load_ohlcv(
        **common,
        timeframe="30m",
        start_utc=_availability_start(raw_start, 30),
        end_utc=end_close,
    )
    return bars_main, bars_sub, bars_execution


def _run_one(
    exchange: str,
    evaluation_start: str,
    raw_start: str,
    end_close: str,
    initial_capital: float,
    fee_rate: float,
    slippage_rate: float,
    invalidation_mode: str,
    reversal_cooldown_bars: int,
):
    bars_main, bars_sub, bars_execution = _load_market_data(
        exchange,
        raw_start,
        end_close,
    )
    funding_rates = None
    if exchange == "binanceusdm":
        funding_rates = load_funding_rates(
            exchange=exchange,
            symbol="BTC/USDT",
            start_utc=evaluation_start,
            end_utc=end_close,
        )

    config = BacktestConfig(
        exchange=exchange,
        symbol="BTC/USDT",
        timeframe_main="30m",
        timeframe_sub="5m",
        execution_timeframe="1m",
        chan_mode="strict_kline8",
        start_utc=evaluation_start,
        end_utc=end_close,
        history_prefetch_days=0,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        macd_divergence_threshold=0.0,
        allow_short_entries=exchange == "binanceusdm",
        check_signal_repaint=True,
        repaint_check_stride=48,
        liquidate_at_end=True,
        invalidation_mode=invalidation_mode,
        reversal_cooldown_bars=reversal_cooldown_bars,
    )
    report = run_backtest(
        config,
        bars_main=bars_main,
        bars_sub=bars_sub,
        bars_execution=bars_execution,
        funding_rates=funding_rates,
    )
    return report, {
        "loaded_rows": {
            "1m_execution": len(bars_execution),
            "5m_confirmation": len(bars_sub),
            "30m_structure_and_execution_clock": len(bars_main),
            "funding": len(funding_rates or []),
        },
        "loaded_ranges": {
            "1m_execution": [
                iso_utc(bars_execution[0].time),
                iso_utc(bars_execution[-1].time),
            ],
            "5m_confirmation": [
                iso_utc(bars_sub[0].time),
                iso_utc(bars_sub[-1].time),
            ],
            "30m_structure_and_execution_clock": [
                iso_utc(bars_main[0].time),
                iso_utc(bars_main[-1].time),
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run strict fixed-clock Chan backtests on BTC 30m/5m"
    )
    parser.add_argument(
        "--market",
        choices=("spot", "perp", "both"),
        default="both",
    )
    parser.add_argument("--raw-start", default="2025-04-15T00:00:00Z")
    parser.add_argument(
        "--evaluation-start",
        default="2025-07-15T00:00:00Z",
    )
    parser.add_argument("--end", default="2026-04-25T00:30:00Z")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--spot-fee", type=float, default=0.001)
    parser.add_argument("--perp-fee", type=float, default=0.0005)
    parser.add_argument("--slippage", type=float, default=0.0002)
    parser.add_argument(
        "--invalidation-mode",
        choices=("intrabar", "close"),
        default="intrabar",
        help="Use strict intrabar invalidation by default; close confirmation remains experimental",
    )
    parser.add_argument(
        "--reversal-cooldown-bars",
        type=int,
        default=1,
        help="Block immediate opposite entry for this many signal bars after a full exit",
    )
    parser.add_argument("--output-dir")
    args = parser.parse_args()

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = Path("outputs") / "btc_30m_clock" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    requested = []
    if args.market in {"spot", "both"}:
        requested.append(("spot", "binance", args.spot_fee))
    if args.market in {"perp", "both"}:
        requested.append(("perp", "binanceusdm", args.perp_fee))

    summary = {
        "generated_at": iso_utc(datetime.now(timezone.utc)),
        "raw_start": args.raw_start,
        "evaluation_start": args.evaluation_start,
        "end": args.end,
        "methodology": {
            "structure_construction": "fixed_clock_30m_with_5m_confirmation",
            "main_structure_level": "clock:30m",
            "sub_confirmation_level": "clock:5m",
            "clock_candles_are_chan_levels": True,
            "entry_execution": "30m close signal -> next 30m open",
            "stop_execution": (
                "first 1m invalidation -> structural stop price, "
                "or 1m open after an adverse gap"
            ),
            "third_class_confirmation": (
                "completed 5m departure + first completed retrace + "
                "next completed 5m bi confirmation"
            ),
            "macd": "12/26/9 auxiliary; exact C area < A area; no percentage threshold",
            "weak_second_class": "recorded but non-executable",
            "liquidate_at_end": True,
            "costs_are_user-configurable_scenarios": True,
            "invalidation_mode": args.invalidation_mode,
            "reversal_cooldown_bars": args.reversal_cooldown_bars,
        },
        "markets": {},
    }
    for name, exchange, fee_rate in requested:
        data_audit = audit(exchange, "BTC/USDT")
        if data_audit["status"] != "ok":
            raise RuntimeError(
                f"data audit failed for {exchange}: {data_audit['failures']}"
            )
        report, data_context = _run_one(
            exchange=exchange,
            evaluation_start=args.evaluation_start,
            raw_start=args.raw_start,
            end_close=args.end,
            initial_capital=args.initial_capital,
            fee_rate=fee_rate,
            slippage_rate=args.slippage,
            invalidation_mode=args.invalidation_mode,
            reversal_cooldown_bars=args.reversal_cooldown_bars,
        )
        report_path = output_dir / f"{name}_report.json"
        report_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary["markets"][name] = {
            "report": str(report_path),
            "metrics": report.metrics,
            "significance": report.significance.to_dict(),
            "pass_checks": report.pass_checks,
            "fail_reasons": report.fail_reasons,
            "signal_repaint_rate": report.signal_repaint_rate,
            "trade_diagnostics": report.trade_diagnostics,
            "data_context": data_context,
            "data_audit": data_audit,
            "cost_assumptions": {
                "fee_rate": fee_rate,
                "slippage_rate": args.slippage,
                "historical_funding_applied": exchange == "binanceusdm",
            },
        }
        print(
            f"[{name}] trades={len(report.trades)} "
            f"return={report.metrics.get('total_return', 0.0):.6f} "
            f"report={report_path}"
        )

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
