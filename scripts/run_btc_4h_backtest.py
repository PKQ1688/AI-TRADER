from __future__ import annotations
# ruff: noqa: E402

import json
from datetime import datetime, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path, write_csv_rows

ensure_src_on_path()

from ai_trader.backtest.engine import run_backtest, run_cost_scenarios, run_sensitivity
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.types import BacktestConfig


def main() -> None:
    config = BacktestConfig(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe_main="4h",
        timeframe_sub="1h",
        start_utc="2022-02-10T00:00:00Z",
        end_utc="2026-02-10T00:00:00Z",
        initial_capital=100000.0,
        fee_rate=0.001,
        slippage_rate=0.0002,
        macd_divergence_threshold=0.10,
        min_confidence=0.60,
        drawdown_reduce_threshold=0.12,
        drawdown_freeze_threshold=0.18,
        freeze_recovery_days=21,
        chan_mode="orthodox_chan",
    )

    bars_main = load_ohlcv(config.exchange, config.symbol, config.timeframe_main, config.start_utc, config.end_utc)
    bars_sub = load_ohlcv(config.exchange, config.symbol, config.timeframe_sub, config.start_utc, config.end_utc)

    base_report = run_backtest(config, bars_main=bars_main, bars_sub=bars_sub)
    cost_reports = run_cost_scenarios(config, bars_main=bars_main, bars_sub=bars_sub)
    sensitivity_reports = run_sensitivity(config, bars_main=bars_main, bars_sub=bars_sub)

    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path("outputs") / "backtest" / "btc_4h_1h" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    write_csv_rows(output_dir / "signals.csv", base_report.signals)
    write_csv_rows(output_dir / "trades.csv", [item.to_dict() for item in base_report.trades])
    write_csv_rows(output_dir / "equity_curve.csv", [item.to_dict() for item in base_report.equity_curve])

    payload = {
        "base": base_report.to_dict(),
        "cost_scenarios": {name: report.to_dict() for name, report in cost_reports.items()},
        "sensitivity": {name: report.to_dict() for name, report in sensitivity_reports.items()},
    }
    (output_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    base = base_report
    summary_lines = [
        "# BTC 4h/1h 缠论回测摘要",
        "",
        f"- 总收益率: {base.metrics.get('total_return', 0.0):.2%}",
        f"- 最大回撤: {base.metrics.get('max_drawdown', 0.0):.2%}",
        f"- 夏普: {base.metrics.get('sharpe', 0.0):.3f}",
        f"- 交易笔数: {int(base.metrics.get('trade_count', 0))}",
        f"- 显著性 p-value: {base.significance.p_value:.4f}",
        f"- 信号重绘率: {base.signal_repaint_rate:.4%}",
        "",
        "## 验收结果",
    ]
    for key, ok in base.pass_checks.items():
        summary_lines.append(f"- {key}: {'PASS' if ok else 'FAIL'}")

    if base.fail_reasons:
        summary_lines.append("")
        summary_lines.append("## 未通过原因")
        for reason in base.fail_reasons:
            summary_lines.append(f"- {reason}")
    else:
        summary_lines.append("")
        summary_lines.append("## 结论")
        summary_lines.append("- 本轮通过既定验收标准。")

    (output_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"Backtest completed. Output: {output_dir}")


if __name__ == "__main__":
    main()
