from __future__ import annotations
# ruff: noqa: E402

import json
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path

ensure_src_on_path()

from ai_trader.backtest.engine import run_backtest
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.types import BacktestConfig


def _action_counter(report) -> dict[str, int]:
    counter = Counter(item.get("action", {}).get("decision", "unknown") for item in report.signals)
    return dict(counter)


def _pick_metrics(report) -> dict[str, float]:
    keys = [
        "total_return",
        "annual_return",
        "max_drawdown",
        "sharpe",
        "win_rate",
        "profit_factor",
        "expectancy",
        "trade_count",
    ]
    return {key: float(report.metrics.get(key, 0.0)) for key in keys}


def _delta(strict: dict[str, float], pragmatic: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in strict.keys():
        out[key] = strict[key] - pragmatic[key]
    return out


def _fmt_pct(v: float) -> str:
    return f"{v:.2%}"


def _fmt_num(v: float) -> str:
    return f"{v:.4f}"


def main() -> None:
    base_config = BacktestConfig(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe_main="4h",
        timeframe_sub="1h",
        chan_mode="strict_kline8",
        start_utc="2024-01-01T00:00:00Z",
        end_utc="2025-12-31T23:59:59Z",
        initial_capital=100000.0,
        fee_rate=0.001,
        slippage_rate=0.0002,
        macd_divergence_threshold=0.10,
        min_confidence=0.60,
        drawdown_reduce_threshold=0.12,
        drawdown_freeze_threshold=0.18,
        freeze_recovery_days=21,
    )

    bars_main = load_ohlcv(base_config.exchange, base_config.symbol, base_config.timeframe_main, base_config.start_utc, base_config.end_utc)
    bars_sub = load_ohlcv(base_config.exchange, base_config.symbol, base_config.timeframe_sub, base_config.start_utc, base_config.end_utc)

    strict_cfg = replace(base_config, chan_mode="strict_kline8")
    pragmatic_cfg = replace(base_config, chan_mode="pragmatic")

    strict_report = run_backtest(strict_cfg, bars_main=bars_main, bars_sub=bars_sub)
    pragmatic_report = run_backtest(pragmatic_cfg, bars_main=bars_main, bars_sub=bars_sub)

    strict_metrics = _pick_metrics(strict_report)
    pragmatic_metrics = _pick_metrics(pragmatic_report)
    metric_delta = _delta(strict_metrics, pragmatic_metrics)

    strict_actions = _action_counter(strict_report)
    pragmatic_actions = _action_counter(pragmatic_report)

    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path("outputs") / "comparison" / "filter_modes" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "strict_config": asdict(strict_cfg),
        "pragmatic_config": asdict(pragmatic_cfg),
        "strict": {
            "metrics": strict_metrics,
            "p_value": strict_report.significance.p_value,
            "signal_repaint_rate": strict_report.signal_repaint_rate,
            "pass_checks": strict_report.pass_checks,
            "fail_reasons": strict_report.fail_reasons,
            "actions": strict_actions,
        },
        "pragmatic": {
            "metrics": pragmatic_metrics,
            "p_value": pragmatic_report.significance.p_value,
            "signal_repaint_rate": pragmatic_report.signal_repaint_rate,
            "pass_checks": pragmatic_report.pass_checks,
            "fail_reasons": pragmatic_report.fail_reasons,
            "actions": pragmatic_actions,
        },
        "delta_strict_minus_pragmatic": {
            "metrics": metric_delta,
            "p_value": strict_report.significance.p_value - pragmatic_report.significance.p_value,
            "signal_repaint_rate": strict_report.signal_repaint_rate - pragmatic_report.signal_repaint_rate,
        },
    }
    (output_dir / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = [
        "# 过滤规则对比（strict_kline8 vs pragmatic）",
        "",
        f"- symbol: {base_config.symbol}",
        f"- timeframe: {base_config.timeframe_main}/{base_config.timeframe_sub}",
        f"- period: {base_config.start_utc} -> {base_config.end_utc}",
        "",
        "## 核心指标",
        "",
        "| 指标 | strict_kline8 | pragmatic | 差值(strict-pragmatic) |",
        "|---|---:|---:|---:|",
        f"| 总收益率 | {_fmt_pct(strict_metrics['total_return'])} | {_fmt_pct(pragmatic_metrics['total_return'])} | {_fmt_pct(metric_delta['total_return'])} |",
        f"| 年化收益 | {_fmt_pct(strict_metrics['annual_return'])} | {_fmt_pct(pragmatic_metrics['annual_return'])} | {_fmt_pct(metric_delta['annual_return'])} |",
        f"| 最大回撤 | {_fmt_pct(strict_metrics['max_drawdown'])} | {_fmt_pct(pragmatic_metrics['max_drawdown'])} | {_fmt_pct(metric_delta['max_drawdown'])} |",
        f"| 夏普 | {_fmt_num(strict_metrics['sharpe'])} | {_fmt_num(pragmatic_metrics['sharpe'])} | {_fmt_num(metric_delta['sharpe'])} |",
        f"| 交易数 | {int(strict_metrics['trade_count'])} | {int(pragmatic_metrics['trade_count'])} | {int(metric_delta['trade_count'])} |",
        f"| 期望收益 | {_fmt_num(strict_metrics['expectancy'])} | {_fmt_num(pragmatic_metrics['expectancy'])} | {_fmt_num(metric_delta['expectancy'])} |",
        "",
        "## 稳定性",
        "",
        f"- p-value: strict={strict_report.significance.p_value:.4f}, pragmatic={pragmatic_report.significance.p_value:.4f}",
        f"- signal_repaint_rate: strict={strict_report.signal_repaint_rate:.4%}, pragmatic={pragmatic_report.signal_repaint_rate:.4%}",
        "",
        "## 动作分布",
        "",
        f"- strict: {strict_actions}",
        f"- pragmatic: {pragmatic_actions}",
    ]
    (output_dir / "summary.md").write_text("\n".join(summary), encoding="utf-8")

    print(f"Comparison completed. Output: {output_dir}")


if __name__ == "__main__":
    main()
