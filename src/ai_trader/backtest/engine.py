from __future__ import annotations

import random
from datetime import timedelta
from dataclasses import replace
from statistics import mean

from ai_trader.chan.config import get_chan_config
from ai_trader.chan.core.buy_sell_points import allow_high_conflict_reversal
from ai_trader.chan import build_chan_state, generate_signal
from ai_trader.chan.engine import suppress_seen_signal_events
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
    parse_utc_time,
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


def _top_signal(
    signals: list[Signal],
    signal_types: set[str],
    min_confidence: float,
    preferred_types: tuple[str, ...] = (),
) -> Signal | None:
    if not signal_types:
        return None
    candidates = [item for item in signals if item.type in signal_types and item.confidence >= min_confidence]
    if not candidates:
        return None
    for preferred in preferred_types:
        typed = [item for item in candidates if item.type == preferred]
        if typed:
            typed.sort(key=lambda item: item.confidence, reverse=True)
            return typed[0]
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates[0]


def _signal_center_key(signal: Signal | None, snapshot) -> tuple[str, int] | None:
    if signal is None or signal.type not in {"B3", "S3"}:
        return None
    if signal.anchor_center_start_index is not None:
        return (signal.type, signal.anchor_center_start_index)
    if snapshot.last_zhongshu_main is not None:
        return (signal.type, snapshot.last_zhongshu_main.start_index)
    return None


def _lookback_start(end_exclusive: int, limit: int) -> int:
    if limit <= 0:
        return 0
    return max(0, end_exclusive - limit)


def _sub_cursor_at_or_before(bars_sub: list[Bar], cursor: int, asof_time) -> int:
    while cursor > 0 and bars_sub[cursor - 1].time > asof_time:
        cursor -= 1
    return cursor


def run_backtest(config: BacktestConfig, bars_main: list[Bar] | None = None, bars_sub: list[Bar] | None = None) -> BacktestReport:
    chan_config = get_chan_config(config.chan_mode)
    buy_entry_types = set(chan_config.execution_buy_types)
    sell_entry_types = set(chan_config.execution_sell_types)
    buy_entry_min_conf = max(config.min_confidence, chan_config.execution_buy_min_confidence)
    sell_entry_min_conf = max(config.min_confidence, chan_config.execution_reduce_min_confidence)
    buy_signal_priority = ("B1", "B2", "B3") if chan_config.prefer_first_class_signals else ()
    sell_signal_priority = ("S1", "S2", "S3") if chan_config.prefer_first_class_signals else ()

    evaluation_start = None
    load_start_utc = config.start_utc
    if bars_main is None or bars_sub is None:
        evaluation_start = parse_utc_time(config.start_utc)
        load_start_utc = iso_utc(
            evaluation_start - timedelta(days=config.history_prefetch_days)
        )

    if bars_main is None:
        bars_main = load_ohlcv(
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe=config.timeframe_main,
            start_utc=load_start_utc,
            end_utc=config.end_utc,
        )
    if bars_sub is None:
        bars_sub = load_ohlcv(
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe=config.timeframe_sub,
            start_utc=load_start_utc,
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
    last_reduce_signature: tuple | None = None
    consumed_buy_center_keys: set[tuple[str, int]] = set()
    consumed_sell_center_keys: set[tuple[str, int]] = set()
    seen_signal_keys: set[tuple] = set()
    turning_signal_guards: dict[tuple, dict[str, object]] = {}

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
    start_index = 120
    if evaluation_start is not None:
        start_index = max(
            120,
            next(
                (i for i, item in enumerate(bars_main) if item.time >= evaluation_start),
                len(bars_main),
            ),
        )

    if start_index >= len(bars_main) - 1:
        empty_sig = evaluate_significance([])
        return BacktestReport(
            config=config,
            metrics={"total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0.0, "expectancy": 0.0},
            segmented_metrics={},
            walk_forward_metrics={},
            significance=empty_sig,
            pass_checks={"data_ready": False},
            fail_reasons=["评估区间不足，无法完成回测"],
            signal_repaint_rate=0.0,
            trades=[],
            signals=[],
            equity_curve=[],
        )

    for i in range(start_index, len(bars_main) - 1):
        bar = bars_main[i]
        next_bar = bars_main[i + 1]

        while sub_cursor < len(bars_sub) and bars_sub[sub_cursor].time <= bar.time:
            sub_cursor += 1

        main_start = _lookback_start(i + 1, config.structure_lookback_main_bars)
        sub_start = _lookback_start(sub_cursor, config.structure_lookback_sub_bars)
        prefix_main = bars_main[main_start : i + 1]
        prefix_sub = bars_sub[sub_start:sub_cursor]
        prefix_macd_main = macd_main_full[main_start : i + 1]
        prefix_macd_sub = macd_sub_full[sub_start:sub_cursor]

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
            macd_main=prefix_macd_main,
            macd_sub=prefix_macd_sub,
            asof_time=bar.time,
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe_main=config.timeframe_main,
            timeframe_sub=config.timeframe_sub,
            chan_config=chan_config,
        )
        raw_decision = generate_signal(
            snapshot=snapshot,
            macd_divergence_threshold=config.macd_divergence_threshold,
            min_confidence=config.min_confidence,
            chan_config=chan_config,
        )
        raw_decision_dict = raw_decision.to_contract_dict()
        decision = suppress_seen_signal_events(
            decision=raw_decision,
            seen_signal_keys=seen_signal_keys,
            chan_config=chan_config,
            min_confidence=config.min_confidence,
            active_turning_guards=turning_signal_guards,
            asof_low=bar.low,
            asof_high=bar.high,
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
        decision_signature = _decision_signature(decision_dict)
        signal_signatures[now_key] = _decision_signature(raw_decision_dict)

        if config.check_signal_repaint and i > 120:
            prev_time = bars_main[i - 1].time
            prev_key = iso_utc(prev_time)
            prev_sub_cursor = _sub_cursor_at_or_before(bars_sub, sub_cursor, prev_time)
            prev_main_start = _lookback_start(i, config.structure_lookback_main_bars)
            prev_sub_start = _lookback_start(prev_sub_cursor, config.structure_lookback_sub_bars)
            prev_prefix_main = bars_main[prev_main_start:i]
            prev_prefix_sub = bars_sub[prev_sub_start:prev_sub_cursor]
            prev_snapshot = build_chan_state(
                bars_main=prev_prefix_main,
                bars_sub=prev_prefix_sub,
                macd_main=macd_main_full[prev_main_start:i],
                macd_sub=macd_sub_full[prev_sub_start:prev_sub_cursor],
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

        buy_signal = _top_signal(
            decision.signals,
            buy_entry_types,
            buy_entry_min_conf,
            preferred_types=buy_signal_priority,
        )
        reduce_signal = _top_signal(
            decision.signals,
            set(chan_config.execution_reduce_types),
            max(config.min_confidence, chan_config.execution_reduce_min_confidence),
            preferred_types=sell_signal_priority,
        )
        sell_signal = _top_signal(
            decision.signals,
            sell_entry_types,
            sell_entry_min_conf,
            preferred_types=sell_signal_priority,
        )
        buy_center_key = _signal_center_key(buy_signal, snapshot)
        sell_center_key = _signal_center_key(sell_signal, snapshot)

        # 冻结恢复双通道
        if frozen:
            has_effective_buy = (
                decision.action.decision == "buy"
                and buy_signal is not None
                and (
                    buy_center_key is None
                    or buy_center_key not in consumed_buy_center_keys
                )
                and (
                    decision.risk.conflict_level != "high"
                    or allow_high_conflict_reversal(buy_signal, decision.market_state)
                )
                and decision.data_quality.status == "ok"
            )
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
        should_close = False
        should_reduce = False

        if position_qty > 0:
            if position_stop_price is not None and bar.close <= position_stop_price:
                should_close = True
            elif decision.action.decision == "sell":
                should_close = True
            elif (
                decision.action.decision == "reduce"
                and reduce_signal is not None
                and decision_signature != last_reduce_signature
            ):
                should_reduce = True
        elif position_qty < 0:
            if position_stop_price is not None and bar.close >= position_stop_price:
                should_close = True
            elif decision.action.decision == "buy":
                should_close = True

        if position_qty != 0 and (should_close or should_reduce):
            is_long = position_qty > 0
            qty_before = abs(position_qty)
            qty_to_close = qty_before if should_close else qty_before * 0.5
            if qty_to_close <= 0:
                qty_to_close = 0.0

            alloc_entry_fee = position_entry_fee * (qty_to_close / qty_before) if qty_before > 0 else 0.0

            if is_long:
                exit_price = next_bar.open * (1 - config.slippage_rate)
                proceeds = qty_to_close * exit_price
                exit_fee = proceeds * config.fee_rate
                cash += proceeds - exit_fee
                gross_pnl = (exit_price - position_entry_price) * qty_to_close
                side = "long"
                slippage_cost = qty_to_close * next_bar.open * config.slippage_rate
            else:
                exit_price = next_bar.open * (1 + config.slippage_rate)
                cover_cost = qty_to_close * exit_price
                exit_fee = cover_cost * config.fee_rate
                cash -= cover_cost + exit_fee
                gross_pnl = (position_entry_price - exit_price) * qty_to_close
                side = "short"
                slippage_cost = qty_to_close * next_bar.open * config.slippage_rate

            net_pnl = gross_pnl - alloc_entry_fee - exit_fee
            notional = position_entry_price * qty_to_close
            net_return = net_pnl / notional if notional > 0 else 0.0

            if position_signal_index >= 0 and position_signal_index + 3 < len(bars_main):
                entry_idx = position_signal_index + 1
                exit_idx = position_signal_index + 3
                fwd_entry = bars_main[entry_idx].open
                forward_long = (bars_main[exit_idx].close - fwd_entry) / fwd_entry if fwd_entry > 0 else 0.0
                forward_return = forward_long if is_long else -forward_long
            else:
                forward_return = 0.0

            benchmark_long = _pick_benchmark_return(rng, year_returns, next_bar.time.year)
            benchmark_return = benchmark_long if is_long else -benchmark_long
            trades.append(
                Trade(
                    side=side,
                    signal_type=position_signal_type,  # type: ignore[arg-type]
                    entry_time=position_entry_time or next_bar.time,
                    exit_time=next_bar.time,
                    entry_price=position_entry_price,
                    exit_price=exit_price,
                    quantity=qty_to_close,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    net_return=net_return,
                    fees=alloc_entry_fee + exit_fee,
                    slippage_cost=slippage_cost,
                    forward_3bar_return=forward_return,
                    benchmark_return=benchmark_return,
                )
            )

            position_entry_fee -= alloc_entry_fee
            if position_entry_fee < 0:
                position_entry_fee = 0.0

            remaining_qty = qty_before - qty_to_close
            if should_close or remaining_qty <= 0:
                position_qty = 0.0
                position_entry_price = 0.0
                position_entry_fee = 0.0
                position_entry_time = None
                position_signal_index = -1
                position_stop_price = None
                last_reduce_signature = None
            else:
                position_qty = remaining_qty if is_long else -remaining_qty
                if should_reduce:
                    last_reduce_signature = decision_signature

            if trades and trades[-1].net_pnl > 0 and recovery_positive_needed > 0:
                recovery_positive_needed -= 1

        # 再处理开仓
        can_open_long = (
            position_qty == 0
            and size_multiplier > 0
            and decision.action.decision == "buy"
            and buy_signal is not None
            and (buy_center_key is None or buy_center_key not in consumed_buy_center_keys)
            and (
                decision.risk.conflict_level != "high"
                or allow_high_conflict_reversal(buy_signal, decision.market_state)
            )
            and decision.data_quality.status == "ok"
        )
        can_open_short = (
            position_qty == 0
            and size_multiplier > 0
            and config.allow_short_entries
            and decision.action.decision == "sell"
            and sell_signal is not None
            and (sell_center_key is None or sell_center_key not in consumed_sell_center_keys)
            and (
                decision.risk.conflict_level != "high"
                or allow_high_conflict_reversal(sell_signal, decision.market_state)
            )
            and decision.data_quality.status == "ok"
        )

        if can_open_long and buy_signal is not None:
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
                last_reduce_signature = None
                if buy_center_key is not None:
                    consumed_buy_center_keys.add(buy_center_key)
        elif can_open_short and sell_signal is not None:
            sell_price = next_bar.open * (1 - config.slippage_rate)
            alloc_notional = cash * size_multiplier / (1 + config.fee_rate)
            if alloc_notional > 0 and sell_price > 0:
                quantity = alloc_notional / sell_price
                entry_fee = alloc_notional * config.fee_rate
                cash += alloc_notional - entry_fee

                position_qty = -quantity
                position_entry_price = sell_price
                position_entry_fee = entry_fee
                position_entry_time = next_bar.time
                position_signal_type = sell_signal.type
                position_signal_index = i
                position_stop_price = sell_signal.invalid_price
                last_reduce_signature = None
                if sell_center_key is not None:
                    consumed_sell_center_keys.add(sell_center_key)

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

    buy_label = "/".join(sorted(buy_entry_types)) if buy_entry_types else "buy"

    sample_count = sum(
        1
        for record in decisions_out
        if record["data_quality"]["status"] == "ok"
        and record["action"]["decision"] == "buy"
        and any(
            item["type"] in buy_entry_types
            and float(item["confidence"]) >= buy_entry_min_conf
            for item in record["signals"]
        )
    )

    buy_forward = [item.forward_3bar_return for item in trades if item.signal_type in buy_entry_types]
    b23_expectation = mean(buy_forward) if buy_forward else 0.0

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
            "sample_count_ge_80": f"有效{buy_label}样本不足80",
            "b23_expectation_gt_0": f"{buy_label}三根主级别前瞻收益期望未大于0",
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
