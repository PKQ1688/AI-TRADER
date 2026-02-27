from __future__ import annotations

import random
from dataclasses import replace
from statistics import mean

from ai_trader.chan.config import get_chan_config
from ai_trader.chan import build_chan_state, generate_signal
from ai_trader.backtest.metrics import calc_metrics, calc_segmented_metrics, calc_walk_forward_metrics
from ai_trader.backtest.significance import evaluate_significance
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.indicators import compute_macd
from ai_trader.types import (
    BacktestConfig,
    BacktestReport,
    Bar,
    EquityPoint,
    Signal,
    Trade,
    iso_utc,
)


def _decision_signature(decision: dict) -> tuple:
    signals = tuple((item["type"], item["level"], round(float(item["confidence"]), 6)) for item in decision["signals"])
    return decision["action"]["decision"], signals, decision["risk"]["conflict_level"]


def _forward_returns_by_year(bars_main: list[Bar]) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    for i in range(0, len(bars_main) - 3):
        entry_idx = i + 1
        exit_idx = i + 3
        if exit_idx >= len(bars_main):
            break
        entry = bars_main[entry_idx].open
        if entry <= 0:
            continue
        ret = (bars_main[exit_idx].close - entry) / entry
        year = bars_main[i].time.year
        out.setdefault(year, []).append(ret)
    return out


def _pick_benchmark_return(rng: random.Random, year_returns: dict[int, list[float]], year: int) -> float:
    candidates = year_returns.get(year)
    if not candidates:
        merged = [item for values in year_returns.values() for item in values]
        if not merged:
            return 0.0
        return merged[rng.randrange(0, len(merged))]
    return candidates[rng.randrange(0, len(candidates))]


def _top_signal(signals: list[Signal], signal_types: set[str], min_confidence: float) -> Signal | None:
    candidates = [item for item in signals if item.type in signal_types and item.confidence >= min_confidence]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates[0]


def run_backtest(config: BacktestConfig, bars_main: list[Bar] | None = None, bars_sub: list[Bar] | None = None) -> BacktestReport:
    chan_config = get_chan_config(config.chan_mode)

    if bars_main is None:
        bars_main = load_ohlcv(
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe=config.timeframe_main,
            start_utc=config.start_utc,
            end_utc=config.end_utc,
        )
    if bars_sub is None:
        bars_sub = load_ohlcv(
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe=config.timeframe_sub,
            start_utc=config.start_utc,
            end_utc=config.end_utc,
        )

    bars_main = sorted(bars_main, key=lambda x: x.time)
    bars_sub = sorted(bars_sub, key=lambda x: x.time)

    if len(bars_main) < 150 or len(bars_sub) < 300:
        empty_sig = evaluate_significance([])
        return BacktestReport(
            config=config,
            metrics={"total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0.0, "expectancy": 0.0},
            segmented_metrics={},
            walk_forward_metrics={},
            significance=empty_sig,
            pass_checks={"data_ready": False},
            fail_reasons=["样本不足，无法完成回测"],
            signal_repaint_rate=0.0,
            trades=[],
            signals=[],
            equity_curve=[],
        )

    macd_main_full = compute_macd(bars_main)
    macd_sub_full = compute_macd(bars_sub)

    cash = config.initial_capital
    position_qty = 0.0
    position_entry_price = 0.0
    position_entry_time = None
    position_entry_fee = 0.0
    position_signal_type = "B2"
    position_signal_index = -1
    position_stop_price: float | None = None

    frozen = False
    freeze_start = None
    freeze_anchor_zhongshu_time = None
    recovery_positive_needed = 0

    peak_equity = config.initial_capital
    repaint_count = 0
    repaint_checks = 0
    signal_signatures: dict[str, tuple] = {}

    decisions_out: list[dict] = []
    trades: list[Trade] = []
    equity_curve: list[EquityPoint] = []

    rng = random.Random(config.random_seed)
    year_returns = _forward_returns_by_year(bars_main)

    sub_cursor = 0

    for i in range(120, len(bars_main) - 1):
        bar = bars_main[i]
        next_bar = bars_main[i + 1]

        while sub_cursor < len(bars_sub) and bars_sub[sub_cursor].time <= bar.time:
            sub_cursor += 1

        prefix_main = bars_main[: i + 1]
        prefix_sub = bars_sub[:sub_cursor]

        # 当前bar收盘权益
        position_value = position_qty * bar.close
        equity = cash + position_value
        if equity > peak_equity:
            peak_equity = equity
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        equity_curve.append(
            EquityPoint(
                time=bar.time,
                equity=equity,
                drawdown=drawdown,
                cash=cash,
                position_value=position_value,
            )
        )

        snapshot = build_chan_state(
            bars_main=prefix_main,
            bars_sub=prefix_sub,
            macd_main=macd_main_full,
            macd_sub=macd_sub_full,
            asof_time=bar.time,
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe_main=config.timeframe_main,
            timeframe_sub=config.timeframe_sub,
            chan_config=chan_config,
        )
        decision = generate_signal(
            snapshot=snapshot,
            macd_divergence_threshold=config.macd_divergence_threshold,
            min_confidence=config.min_confidence,
            chan_config=chan_config,
        )
        decision_dict = decision.to_contract_dict()
        decision_dict["time"] = iso_utc(bar.time)
        decisions_out.append(decision_dict)

        if drawdown >= config.drawdown_freeze_threshold and not frozen:
            frozen = True
            freeze_start = bar.time
            freeze_anchor_zhongshu_time = (
                snapshot.last_zhongshu_main.available_time if snapshot.last_zhongshu_main else None
            )

        now_key = iso_utc(bar.time)
        signal_signatures[now_key] = _decision_signature(decision_dict)

        if i > 120:
            prev_time = bars_main[i - 1].time
            prev_key = iso_utc(prev_time)
            prev_snapshot = build_chan_state(
                bars_main=prefix_main,
                bars_sub=prefix_sub,
                macd_main=macd_main_full,
                macd_sub=macd_sub_full,
                asof_time=prev_time,
                exchange=config.exchange,
                symbol=config.symbol,
                timeframe_main=config.timeframe_main,
                timeframe_sub=config.timeframe_sub,
                chan_config=chan_config,
            )
            prev_decision = generate_signal(
                snapshot=prev_snapshot,
                macd_divergence_threshold=config.macd_divergence_threshold,
                min_confidence=config.min_confidence,
                chan_config=chan_config,
            ).to_contract_dict()
            if prev_key in signal_signatures:
                repaint_checks += 1
                if _decision_signature(prev_decision) != signal_signatures[prev_key]:
                    repaint_count += 1

        # 冻结恢复双通道
        if frozen:
            has_effective_buy = any(item.type in {"B2", "B3"} and item.confidence >= config.min_confidence for item in decision.signals)
            newer_zhongshu = (
                snapshot.last_zhongshu_main is not None
                and (
                    freeze_anchor_zhongshu_time is None
                    or snapshot.last_zhongshu_main.available_time > freeze_anchor_zhongshu_time
                )
            )
            channel_a = has_effective_buy and newer_zhongshu
            channel_b = False
            if freeze_start is not None:
                days_frozen = (bar.time - freeze_start).days
                channel_b = days_frozen >= config.freeze_recovery_days and drawdown < config.drawdown_reduce_threshold

            if channel_a or channel_b:
                frozen = False
                freeze_start = None
                freeze_anchor_zhongshu_time = None
                recovery_positive_needed = 2

        size_multiplier = 1.0
        if drawdown >= config.drawdown_reduce_threshold:
            size_multiplier = config.reduce_ratio
        if frozen:
            size_multiplier = 0.0
        if recovery_positive_needed > 0:
            size_multiplier = min(size_multiplier, 0.5)

        # 先处理平仓/减仓（t信号，t+1开盘执行）
        should_sell = False
        should_reduce = False
        sell_signal = _top_signal(decision.signals, {"S2", "S3"}, config.min_confidence)

        if position_qty > 0:
            if position_stop_price is not None and bar.close <= position_stop_price:
                should_sell = True
            elif decision.action.decision == "sell" or sell_signal is not None:
                should_sell = True
            elif decision.action.decision == "reduce":
                should_reduce = True

        if position_qty > 0 and (should_sell or should_reduce):
            qty_to_sell = position_qty if should_sell else position_qty * 0.5
            sell_price = next_bar.open * (1 - config.slippage_rate)
            proceeds = qty_to_sell * sell_price
            exit_fee = proceeds * config.fee_rate
            cash += proceeds - exit_fee

            qty_before = position_qty
            alloc_entry_fee = position_entry_fee * (qty_to_sell / qty_before) if qty_before > 0 else 0.0
            gross_pnl = (sell_price - position_entry_price) * qty_to_sell
            net_pnl = gross_pnl - alloc_entry_fee - exit_fee
            notional = position_entry_price * qty_to_sell
            net_return = net_pnl / notional if notional > 0 else 0.0

            if position_signal_index >= 0 and position_signal_index + 3 < len(bars_main):
                entry_idx = position_signal_index + 1
                exit_idx = position_signal_index + 3
                fwd_entry = bars_main[entry_idx].open
                forward_return = (bars_main[exit_idx].close - fwd_entry) / fwd_entry if fwd_entry > 0 else 0.0
            else:
                forward_return = 0.0

            benchmark_return = _pick_benchmark_return(rng, year_returns, next_bar.time.year)
            trades.append(
                Trade(
                    side="long",
                    signal_type=position_signal_type,  # type: ignore[arg-type]
                    entry_time=position_entry_time or next_bar.time,
                    exit_time=next_bar.time,
                    entry_price=position_entry_price,
                    exit_price=sell_price,
                    quantity=qty_to_sell,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    net_return=net_return,
                    fees=alloc_entry_fee + exit_fee,
                    slippage_cost=qty_to_sell * next_bar.open * config.slippage_rate,
                    forward_3bar_return=forward_return,
                    benchmark_return=benchmark_return,
                )
            )

            position_qty -= qty_to_sell
            position_entry_fee -= alloc_entry_fee

            if should_sell or position_qty <= 0:
                position_qty = 0.0
                position_entry_price = 0.0
                position_entry_fee = 0.0
                position_entry_time = None
                position_signal_index = -1
                position_stop_price = None

            if trades and trades[-1].net_pnl > 0 and recovery_positive_needed > 0:
                recovery_positive_needed -= 1

        # 再处理开仓
        buy_signal = _top_signal(decision.signals, {"B2", "B3"}, config.min_confidence)
        can_open = (
            position_qty <= 0
            and size_multiplier > 0
            and decision.action.decision == "buy"
            and buy_signal is not None
            and decision.risk.conflict_level != "high"
            and decision.data_quality.status == "ok"
        )

        if can_open:
            buy_price = next_bar.open * (1 + config.slippage_rate)
            alloc_cash = cash * size_multiplier / (1 + config.fee_rate)
            if alloc_cash > 0 and buy_price > 0:
                quantity = alloc_cash / buy_price
                entry_fee = alloc_cash * config.fee_rate
                cash -= alloc_cash + entry_fee

                position_qty = quantity
                position_entry_price = buy_price
                position_entry_fee = entry_fee
                position_entry_time = next_bar.time
                position_signal_type = buy_signal.type
                position_signal_index = i
                position_stop_price = buy_signal.invalid_price

    # 最后一个bar补权益
    if bars_main:
        last = bars_main[-1]
        position_value = position_qty * last.close
        equity = cash + position_value
        if equity > peak_equity:
            peak_equity = equity
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        equity_curve.append(
            EquityPoint(time=last.time, equity=equity, drawdown=drawdown, cash=cash, position_value=position_value)
        )

    significance = evaluate_significance(trades=trades, benchmark=config.benchmark, random_seed=config.random_seed)

    metrics = calc_metrics(equity_curve=equity_curve, trades=trades, initial_capital=config.initial_capital)
    segmented_metrics = calc_segmented_metrics(equity_curve=equity_curve, trades=trades, initial_capital=config.initial_capital)
    walk_forward_metrics = calc_walk_forward_metrics(equity_curve=equity_curve, trades=trades, initial_capital=config.initial_capital)

    sample_count = sum(
        1
        for record in decisions_out
        for item in record["signals"]
        if item["type"] in {"B2", "B3"} and float(item["confidence"]) >= config.min_confidence
    )

    b23_forward = [item.forward_3bar_return for item in trades if item.signal_type in {"B2", "B3"}]
    b23_expectation = mean(b23_forward) if b23_forward else 0.0

    signal_repaint_rate = repaint_count / repaint_checks if repaint_checks > 0 else 0.0

    pass_checks = {
        "sample_count_ge_80": sample_count >= 80,
        "b23_expectation_gt_0": b23_expectation > 0,
        "p_value_lt_0_05": significance.p_value < 0.05,
        "max_drawdown_le_0_25": metrics.get("max_drawdown", 1.0) <= 0.25,
        "signal_repaint_rate_eq_0": signal_repaint_rate == 0.0,
    }

    fail_reasons = [
        reason
        for key, reason in {
            "sample_count_ge_80": "有效B2/B3样本不足80",
            "b23_expectation_gt_0": "B2/B3三根4h前瞻收益期望未大于0",
            "p_value_lt_0_05": "相对时间匹配随机基线未达到统计显著(p>=0.05)",
            "max_drawdown_le_0_25": "最大回撤超过25%",
            "signal_repaint_rate_eq_0": "检测到信号重绘",
        }.items()
        if not pass_checks.get(key, False)
    ]

    return BacktestReport(
        config=config,
        metrics=metrics,
        segmented_metrics=segmented_metrics,
        walk_forward_metrics=walk_forward_metrics,
        significance=significance,
        pass_checks=pass_checks,
        fail_reasons=fail_reasons,
        signal_repaint_rate=signal_repaint_rate,
        trades=trades,
        signals=decisions_out,
        equity_curve=equity_curve,
    )


def run_cost_scenarios(config: BacktestConfig, bars_main: list[Bar], bars_sub: list[Bar]) -> dict[str, BacktestReport]:
    scenarios = {
        "base": config,
        "stress_1": replace(config, fee_rate=0.0015, slippage_rate=0.0005),
        "stress_2": replace(config, fee_rate=0.0020, slippage_rate=0.0010),
    }
    return {name: run_backtest(cfg, bars_main=bars_main, bars_sub=bars_sub) for name, cfg in scenarios.items()}


def run_sensitivity(config: BacktestConfig, bars_main: list[Bar], bars_sub: list[Bar]) -> dict[str, BacktestReport]:
    reports: dict[str, BacktestReport] = {}
    dd_pairs = [(0.10, 0.15), (0.12, 0.18), (0.15, 0.25)]
    macd_factors = [0.8, 1.0, 1.2]

    for reduce_dd, freeze_dd in dd_pairs:
        for factor in macd_factors:
            key = f"dd_{int(reduce_dd*100)}_{int(freeze_dd*100)}_macd_{factor:.1f}"
            cfg = replace(
                config,
                drawdown_reduce_threshold=reduce_dd,
                drawdown_freeze_threshold=freeze_dd,
                macd_divergence_threshold=config.macd_divergence_threshold * factor,
            )
            reports[key] = run_backtest(cfg, bars_main=bars_main, bars_sub=bars_sub)

    return reports
