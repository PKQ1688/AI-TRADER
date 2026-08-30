from __future__ import annotations

import random
from datetime import timedelta
from dataclasses import replace
from statistics import mean

from ai_trader.chan.config import get_chan_config
from ai_trader.chan.core.buy_sell_points import allow_high_conflict_reversal
from ai_trader.chan import (
    StructuralReplay,
    build_chan_state,
    build_structural_seed,
    generate_signal,
)
from ai_trader.chan.engine import suppress_seen_signal_events
from ai_trader.backtest.metrics import (
    calc_metrics,
    calc_segmented_metrics,
    calc_trade_diagnostics,
    calc_walk_forward_metrics,
)
from ai_trader.backtest.significance import evaluate_paired_returns
from ai_trader.data import load_funding_rates, load_ohlcv
from ai_trader.indicators import compute_macd
from ai_trader.types import (
    BacktestConfig,
    BacktestReport,
    Bar,
    EquityPoint,
    FundingRate,
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
    candidates = [
        item
        for item in signals
        if item.executable
        and item.type in signal_types
        and item.confidence >= min_confidence
    ]
    if not candidates:
        return None
    for preferred in preferred_types:
        typed = [item for item in candidates if item.type == preferred]
        if typed:
            typed.sort(key=lambda item: item.confidence, reverse=True)
            return typed[0]
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates[0]


def _signal_center_key(signal: Signal | None, snapshot) -> tuple[str, object] | None:
    if signal is None or signal.type not in {"B3", "S3"}:
        return None
    if signal.anchor_center_available_time is not None:
        return (signal.type, signal.anchor_center_available_time)
    if signal.anchor_center_start_index is not None:
        return (signal.type, signal.anchor_center_start_index)
    if snapshot.last_zhongshu_main is not None:
        center_start_index = getattr(
            snapshot.last_zhongshu_main, "start_index", None
        )
        if center_start_index is not None:
            return (signal.type, center_start_index)
        center_available_time = (
            getattr(snapshot.last_zhongshu_main, "origin_available_time", None)
            or getattr(snapshot.last_zhongshu_main, "available_time", None)
        )
        if center_available_time is not None:
            return (signal.type, center_available_time)
    return None


def _lookback_start(end_exclusive: int, limit: int) -> int:
    if limit <= 0:
        return 0
    return max(0, end_exclusive - limit)


def _sub_cursor_at_or_before(bars_sub: list[Bar], cursor: int, asof_time) -> int:
    while cursor > 0 and bars_sub[cursor - 1].time > asof_time:
        cursor -= 1
    return cursor


def _position_invalidated(
    position_qty: float,
    invalid_price: float | None,
    bar: Bar,
    mode: str,
) -> bool:
    if position_qty == 0 or invalid_price is None:
        return False
    if position_qty > 0:
        observed = bar.close if mode == "close" else bar.low
        return observed < invalid_price
    observed = bar.close if mode == "close" else bar.high
    return observed > invalid_price


def _stop_reference_price(
    position_qty: float,
    invalid_price: float,
    bar: Bar,
    mode: str,
) -> float:
    if mode == "close":
        return bar.close
    if position_qty > 0:
        return min(invalid_price, bar.open)
    return max(invalid_price, bar.open)


def _bar_excursions(
    position_qty: float,
    entry_price: float,
    bar: Bar,
) -> tuple[float, float, float, float]:
    if position_qty > 0:
        favorable_price = bar.high
        adverse_price = bar.low
        favorable = favorable_price / entry_price - 1
        adverse = adverse_price / entry_price - 1
    else:
        favorable_price = bar.low
        adverse_price = bar.high
        favorable = (entry_price - favorable_price) / entry_price
        adverse = (entry_price - adverse_price) / entry_price
    return favorable, adverse, favorable_price, adverse_price


def run_backtest(
    config: BacktestConfig,
    bars_main: list[Bar] | None = None,
    bars_sub: list[Bar] | None = None,
    bars_structure: list[Bar] | None = None,
    bars_execution: list[Bar] | None = None,
    funding_rates: list[FundingRate] | None = None,
) -> BacktestReport:
    chan_config = get_chan_config(config.chan_mode)
    buy_entry_types = set(chan_config.execution_buy_types)
    sell_entry_types = set(chan_config.execution_sell_types)
    buy_entry_min_conf = max(config.min_confidence, chan_config.execution_buy_min_confidence)
    sell_entry_min_conf = max(config.min_confidence, chan_config.execution_reduce_min_confidence)
    buy_signal_priority = ("B1", "B2", "B3") if chan_config.prefer_first_class_signals else ()
    sell_signal_priority = ("S1", "S2", "S3") if chan_config.prefer_first_class_signals else ()

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
    if chan_config.structure_construction == "recursive_1m" and bars_structure is None:
        bars_structure = load_ohlcv(
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe=chan_config.structure_level_names[0],
            start_utc=load_start_utc,
            end_utc=config.end_utc,
        )
    if config.execution_timeframe is not None and bars_execution is None:
        bars_execution = load_ohlcv(
            exchange=config.exchange,
            symbol=config.symbol,
            timeframe=config.execution_timeframe,
            start_utc=load_start_utc,
            end_utc=config.end_utc,
        )
    if config.exchange.lower() == "binanceusdm" and funding_rates is None:
        funding_rates = load_funding_rates(
            exchange=config.exchange,
            symbol=config.symbol,
            start_utc=config.start_utc,
            end_utc=config.end_utc,
        )

    bars_main = sorted(bars_main, key=lambda x: x.time)
    bars_sub = sorted(bars_sub, key=lambda x: x.time)
    if bars_structure is not None:
        bars_structure = sorted(bars_structure, key=lambda x: x.time)
    if bars_execution is not None:
        bars_execution = sorted(bars_execution, key=lambda x: x.time)
    funding_rates = sorted(funding_rates or [], key=lambda x: x.time)

    structural_replay = None
    if chan_config.structure_construction == "recursive_1m":
        if not bars_structure:
            empty_sig = evaluate_paired_returns([], [])
            return BacktestReport(
                config=config,
                metrics={"total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0.0, "expectancy": 0.0},
                segmented_metrics={},
                walk_forward_metrics={},
                significance=empty_sig,
                pass_checks={"data_ready": False},
                fail_reasons=["严格递归模式缺少 1m 数据，无法完成回测"],
                signal_repaint_rate=0.0,
                trades=[],
                signals=[],
                equity_curve=[],
            )
        structural_seed = build_structural_seed(
            bars_structure,
            timeframe=chan_config.structure_level_names[0],
            min_stroke_bars=chan_config.min_stroke_bars,
            allow_equal_fractal=chan_config.allow_equal_fractal,
            require_case2_confirmation=chan_config.require_case2_confirmation,
        )
        structural_replay = StructuralReplay(
            structural_seed,
            target_level=chan_config.structure_target_level,
            level_names=chan_config.structure_level_names,
        )

    if len(bars_main) < 150 or len(bars_sub) < 300:
        empty_sig = evaluate_paired_returns([], [])
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
    position_entry_slippage = 0.0
    position_funding_pnl = 0.0
    position_signal_type = "B2"
    position_signal_index = -1
    position_stop_price: float | None = None
    position_signal_event_time = None
    position_signal_available_time = None
    position_mfe = 0.0
    position_mae = 0.0
    position_mfe_time = None
    position_mae_time = None
    position_mfe_price: float | None = None
    position_mae_price: float | None = None
    last_reduce_signature: tuple | None = None
    reversal_blocked_side: str | None = None
    reversal_blocked_until_index = -1
    consumed_buy_center_keys: set[tuple[str, object]] = set()
    consumed_sell_center_keys: set[tuple[str, object]] = set()
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
    entry_signal_forward_returns: list[float] = []
    entry_signal_benchmark_returns: list[float] = []

    signal_benchmark_rng = random.Random(config.random_seed)
    trade_benchmark_rng = random.Random(config.random_seed + 1)
    year_returns = _forward_returns_by_year(bars_main)

    sub_cursor = 0
    execution_cursor = 0
    funding_cursor = 0
    start_index = 120
    start_index = max(
        120,
        next(
            (i for i, item in enumerate(bars_main) if item.time >= evaluation_start),
            len(bars_main),
        ),
    )

    if start_index >= len(bars_main) - 1:
        empty_sig = evaluate_paired_returns([], [])
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

        execution_window: list[Bar] = []
        if bars_execution:
            execution_start = execution_cursor
            while (
                execution_cursor < len(bars_execution)
                and bars_execution[execution_cursor].time <= bar.time
            ):
                execution_cursor += 1
            execution_window = bars_execution[execution_start:execution_cursor]

        execution_stop_bar = None
        if (
            execution_window
            and position_qty != 0
            and position_entry_price > 0
            and position_entry_time is not None
        ):
            for execution_bar in execution_window:
                if execution_bar.time <= position_entry_time:
                    continue
                favorable, adverse, favorable_price, adverse_price = (
                    _bar_excursions(
                        position_qty,
                        position_entry_price,
                        execution_bar,
                    )
                )
                if favorable > position_mfe:
                    position_mfe = favorable
                    position_mfe_time = execution_bar.time
                    position_mfe_price = favorable_price
                if adverse < position_mae:
                    position_mae = adverse
                    position_mae_time = execution_bar.time
                    position_mae_price = adverse_price
                if _position_invalidated(
                    position_qty,
                    position_stop_price,
                    execution_bar,
                    config.invalidation_mode,
                ):
                    execution_stop_bar = execution_bar
                    break

        if execution_stop_bar is not None and position_stop_price is not None:
            while (
                funding_cursor < len(funding_rates)
                and funding_rates[funding_cursor].time <= execution_stop_bar.time
            ):
                funding = funding_rates[funding_cursor]
                if (
                    position_entry_time is not None
                    and funding.time > position_entry_time
                ):
                    mark_price = (
                        funding.mark_price
                        if funding.mark_price > 0
                        else execution_stop_bar.close
                    )
                    funding_pnl = -position_qty * mark_price * funding.rate
                    cash += funding_pnl
                    position_funding_pnl += funding_pnl
                funding_cursor += 1

            is_long = position_qty > 0
            qty_to_close = abs(position_qty)
            reference_price = _stop_reference_price(
                position_qty,
                position_stop_price,
                execution_stop_bar,
                config.invalidation_mode,
            )
            if is_long:
                exit_price = reference_price * (1 - config.slippage_rate)
                proceeds = qty_to_close * exit_price
                exit_fee = proceeds * config.fee_rate
                cash += proceeds - exit_fee
                gross_pnl = (
                    exit_price - position_entry_price
                ) * qty_to_close
                side = "long"
            else:
                exit_price = reference_price * (1 + config.slippage_rate)
                cover_cost = qty_to_close * exit_price
                exit_fee = cover_cost * config.fee_rate
                cash -= cover_cost + exit_fee
                gross_pnl = (
                    position_entry_price - exit_price
                ) * qty_to_close
                side = "short"
            exit_slippage = (
                qty_to_close * reference_price * config.slippage_rate
            )
            net_pnl = (
                gross_pnl
                - position_entry_fee
                - exit_fee
                + position_funding_pnl
            )
            notional = position_entry_price * qty_to_close
            net_return = net_pnl / notional if notional > 0 else 0.0

            if (
                position_signal_index >= 0
                and position_signal_index + 3 < len(bars_main)
            ):
                entry_idx = position_signal_index + 1
                exit_idx = position_signal_index + 3
                fwd_entry = bars_main[entry_idx].open
                forward_long = (
                    (bars_main[exit_idx].close - fwd_entry) / fwd_entry
                    if fwd_entry > 0
                    else 0.0
                )
                forward_return = forward_long if is_long else -forward_long
            else:
                forward_return = 0.0

            benchmark_long = _pick_benchmark_return(
                trade_benchmark_rng,
                year_returns,
                (position_entry_time or execution_stop_bar.time).year,
            )
            benchmark_return = benchmark_long if is_long else -benchmark_long
            trades.append(
                Trade(
                    side=side,
                    signal_type=position_signal_type,  # type: ignore[arg-type]
                    entry_time=position_entry_time or execution_stop_bar.time,
                    exit_time=execution_stop_bar.time,
                    entry_price=position_entry_price,
                    exit_price=exit_price,
                    quantity=qty_to_close,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    net_return=net_return,
                    fees=position_entry_fee + exit_fee,
                    slippage_cost=position_entry_slippage + exit_slippage,
                    forward_3bar_return=forward_return,
                    benchmark_return=benchmark_return,
                    funding_pnl=position_funding_pnl,
                    exit_reason="invalidated",
                    signal_event_time=position_signal_event_time,
                    signal_available_time=position_signal_available_time,
                    invalid_price=position_stop_price,
                    max_favorable_excursion=position_mfe,
                    max_adverse_excursion=position_mae,
                    mfe_time=position_mfe_time,
                    mae_time=position_mae_time,
                    mfe_price=position_mfe_price,
                    mae_price=position_mae_price,
                )
            )

            reversal_blocked_side = "short" if is_long else "long"
            reversal_blocked_until_index = i + config.reversal_cooldown_bars
            position_qty = 0.0
            position_entry_price = 0.0
            position_entry_fee = 0.0
            position_entry_slippage = 0.0
            position_funding_pnl = 0.0
            position_entry_time = None
            position_signal_index = -1
            position_stop_price = None
            position_signal_event_time = None
            position_signal_available_time = None
            position_mfe = 0.0
            position_mae = 0.0
            position_mfe_time = None
            position_mae_time = None
            position_mfe_price = None
            position_mae_price = None
            last_reduce_signature = None
            if trades[-1].net_pnl > 0 and recovery_positive_needed > 0:
                recovery_positive_needed -= 1

        if (
            not bars_execution
            and position_qty != 0
            and position_entry_price > 0
            and position_entry_time is not None
            and bar.time > position_entry_time
        ):
            favorable, adverse, favorable_price, adverse_price = (
                _bar_excursions(position_qty, position_entry_price, bar)
            )
            if favorable > position_mfe:
                position_mfe = favorable
                position_mfe_time = bar.time
                position_mfe_price = favorable_price
            if adverse < position_mae:
                position_mae = adverse
                position_mae_time = bar.time
                position_mae_price = adverse_price

        # Binance settles funding at the published funding timestamp.  A
        # position opened at this same timestamp is treated as opening after
        # settlement; an existing position is settled before any t+1-open
        # execution triggered by the just-closed bar.
        while (
            funding_cursor < len(funding_rates)
            and funding_rates[funding_cursor].time <= bar.time
        ):
            funding = funding_rates[funding_cursor]
            if (
                position_qty != 0
                and position_entry_time is not None
                and funding.time > position_entry_time
            ):
                mark_price = (
                    funding.mark_price if funding.mark_price > 0 else bar.close
                )
                funding_pnl = -position_qty * mark_price * funding.rate
                cash += funding_pnl
                position_funding_pnl += funding_pnl
            funding_cursor += 1

        while sub_cursor < len(bars_sub) and bars_sub[sub_cursor].time <= bar.time:
            sub_cursor += 1

        main_start = _lookback_start(i + 1, config.structure_lookback_main_bars)
        sub_start = _lookback_start(sub_cursor, config.structure_lookback_sub_bars)
        prefix_main = bars_main[main_start : i + 1]
        prefix_sub = bars_sub[sub_start:sub_cursor]
        # Strict Chan divergence compares the complete A/C trend intervals.
        # The structural K-line lookback may stay bounded for performance, but
        # truncating MACD at that same boundary can silently remove part of A.
        prefix_macd_main = (
            macd_main_full[: i + 1]
            if chan_config.mode == "strict_recursive"
            else macd_main_full[main_start : i + 1]
        )
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
            structural_view=(
                structural_replay.view_at(bar.time)
                if structural_replay is not None
                else None
            ),
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

        if (
            config.check_signal_repaint
            and i > 120
            and (i - start_index) % max(1, config.repaint_check_stride) == 0
        ):
            prev_time = bars_main[i - 1].time
            prev_key = iso_utc(prev_time)
            prev_sub_cursor = _sub_cursor_at_or_before(bars_sub, sub_cursor, prev_time)
            prev_main_start = _lookback_start(i, config.structure_lookback_main_bars)
            prev_sub_start = _lookback_start(prev_sub_cursor, config.structure_lookback_sub_bars)
            prev_prefix_main = bars_main[prev_main_start:i]
            prev_prefix_sub = bars_sub[prev_sub_start:prev_sub_cursor]
            prev_macd_main = (
                macd_main_full[:i]
                if chan_config.mode == "strict_recursive"
                else macd_main_full[prev_main_start:i]
            )
            prev_snapshot = build_chan_state(
                bars_main=prev_prefix_main,
                bars_sub=prev_prefix_sub,
                macd_main=prev_macd_main,
                macd_sub=macd_sub_full[prev_sub_start:prev_sub_cursor],
                asof_time=prev_time,
                exchange=config.exchange,
                symbol=config.symbol,
                timeframe_main=config.timeframe_main,
                timeframe_sub=config.timeframe_sub,
                chan_config=chan_config,
                structural_view=(
                    structural_replay.view_at(prev_time)
                    if structural_replay is not None
                    else None
                ),
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

        effective_buy_sample = (
            decision.data_quality.status == "ok"
            and decision.action.decision == "buy"
            and buy_signal is not None
            and (
                decision.risk.conflict_level != "high"
                or allow_high_conflict_reversal(buy_signal, decision.market_state)
            )
        )
        effective_sell_sample = (
            decision.data_quality.status == "ok"
            and config.allow_short_entries
            and decision.action.decision == "sell"
            and sell_signal is not None
            and (
                decision.risk.conflict_level != "high"
                or allow_high_conflict_reversal(sell_signal, decision.market_state)
            )
        )
        sample_side = (
            "long"
            if effective_buy_sample
            else "short"
            if effective_sell_sample
            else None
        )
        if sample_side is not None and i + 3 < len(bars_main):
            forward_entry = bars_main[i + 1].open
            forward_exit = bars_main[i + 3].close
            forward_long_return = (
                (forward_exit - forward_entry) / forward_entry
                if forward_entry > 0
                else 0.0
            )
            benchmark_long_return = _pick_benchmark_return(
                signal_benchmark_rng,
                year_returns,
                bars_main[i + 1].time.year,
            )
            direction = 1.0 if sample_side == "long" else -1.0
            entry_signal_forward_returns.append(
                direction * forward_long_return
            )
            entry_signal_benchmark_returns.append(
                direction * benchmark_long_return
            )

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
        close_reason = "signal"

        if position_qty > 0:
            if (
                not bars_execution
                and _position_invalidated(
                    position_qty,
                    position_stop_price,
                    bar,
                    config.invalidation_mode,
                )
            ):
                should_close = True
                close_reason = "invalidated"
            elif decision.action.decision == "sell":
                should_close = True
            elif (
                decision.action.decision == "reduce"
                and reduce_signal is not None
                and decision_signature != last_reduce_signature
            ):
                should_reduce = True
        elif position_qty < 0:
            if (
                not bars_execution
                and _position_invalidated(
                    position_qty,
                    position_stop_price,
                    bar,
                    config.invalidation_mode,
                )
            ):
                should_close = True
                close_reason = "invalidated"
            elif decision.action.decision == "buy":
                should_close = True

        if position_qty != 0 and (should_close or should_reduce):
            is_long = position_qty > 0
            qty_before = abs(position_qty)
            qty_to_close = qty_before if should_close else qty_before * 0.5
            if qty_to_close <= 0:
                qty_to_close = 0.0

            alloc_entry_fee = position_entry_fee * (qty_to_close / qty_before) if qty_before > 0 else 0.0
            alloc_entry_slippage = (
                position_entry_slippage * (qty_to_close / qty_before)
                if qty_before > 0
                else 0.0
            )
            alloc_funding_pnl = (
                position_funding_pnl * (qty_to_close / qty_before)
                if qty_before > 0
                else 0.0
            )

            if is_long:
                exit_price = next_bar.open * (1 - config.slippage_rate)
                proceeds = qty_to_close * exit_price
                exit_fee = proceeds * config.fee_rate
                cash += proceeds - exit_fee
                gross_pnl = (exit_price - position_entry_price) * qty_to_close
                side = "long"
                exit_slippage = qty_to_close * next_bar.open * config.slippage_rate
            else:
                exit_price = next_bar.open * (1 + config.slippage_rate)
                cover_cost = qty_to_close * exit_price
                exit_fee = cover_cost * config.fee_rate
                cash -= cover_cost + exit_fee
                gross_pnl = (position_entry_price - exit_price) * qty_to_close
                side = "short"
                exit_slippage = qty_to_close * next_bar.open * config.slippage_rate

            net_pnl = (
                gross_pnl
                - alloc_entry_fee
                - exit_fee
                + alloc_funding_pnl
            )
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

            benchmark_long = _pick_benchmark_return(
                trade_benchmark_rng,
                year_returns,
                (position_entry_time or bar.time).year,
            )
            benchmark_return = benchmark_long if is_long else -benchmark_long
            trades.append(
                Trade(
                    side=side,
                    signal_type=position_signal_type,  # type: ignore[arg-type]
                    entry_time=position_entry_time or bar.time,
                    exit_time=bar.time,
                    entry_price=position_entry_price,
                    exit_price=exit_price,
                    quantity=qty_to_close,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    net_return=net_return,
                    fees=alloc_entry_fee + exit_fee,
                    slippage_cost=alloc_entry_slippage + exit_slippage,
                    forward_3bar_return=forward_return,
                    benchmark_return=benchmark_return,
                    funding_pnl=alloc_funding_pnl,
                    exit_reason=(
                        close_reason if should_close else "risk_reduce"
                    ),
                    signal_event_time=position_signal_event_time,
                    signal_available_time=position_signal_available_time,
                    invalid_price=position_stop_price,
                    max_favorable_excursion=position_mfe,
                    max_adverse_excursion=position_mae,
                    mfe_time=position_mfe_time,
                    mae_time=position_mae_time,
                    mfe_price=position_mfe_price,
                    mae_price=position_mae_price,
                )
            )

            position_entry_fee -= alloc_entry_fee
            if position_entry_fee < 0:
                position_entry_fee = 0.0
            position_entry_slippage -= alloc_entry_slippage
            if position_entry_slippage < 0:
                position_entry_slippage = 0.0
            position_funding_pnl -= alloc_funding_pnl
            if abs(position_funding_pnl) < 1e-12:
                position_funding_pnl = 0.0

            remaining_qty = qty_before - qty_to_close
            if should_close or remaining_qty <= 0:
                if should_close:
                    reversal_blocked_side = "short" if is_long else "long"
                    reversal_blocked_until_index = (
                        i + config.reversal_cooldown_bars
                    )
                position_qty = 0.0
                position_entry_price = 0.0
                position_entry_fee = 0.0
                position_entry_slippage = 0.0
                position_funding_pnl = 0.0
                position_entry_time = None
                position_signal_index = -1
                position_stop_price = None
                position_signal_event_time = None
                position_signal_available_time = None
                position_mfe = 0.0
                position_mae = 0.0
                position_mfe_time = None
                position_mae_time = None
                position_mfe_price = None
                position_mae_price = None
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
            and not (
                reversal_blocked_side == "long"
                and i < reversal_blocked_until_index
            )
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
            and not (
                reversal_blocked_side == "short"
                and i < reversal_blocked_until_index
            )
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
                position_entry_slippage = (
                    quantity * next_bar.open * config.slippage_rate
                )
                position_entry_time = bar.time
                position_signal_type = buy_signal.type
                position_signal_index = i
                position_stop_price = buy_signal.invalid_price
                position_signal_event_time = buy_signal.event_time
                position_signal_available_time = buy_signal.available_time
                position_mfe = 0.0
                position_mae = 0.0
                position_mfe_time = None
                position_mae_time = None
                position_mfe_price = None
                position_mae_price = None
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
                position_entry_slippage = (
                    quantity * next_bar.open * config.slippage_rate
                )
                position_entry_time = bar.time
                position_signal_type = sell_signal.type
                position_signal_index = i
                position_stop_price = sell_signal.invalid_price
                position_signal_event_time = sell_signal.event_time
                position_signal_available_time = sell_signal.available_time
                position_mfe = 0.0
                position_mae = 0.0
                position_mfe_time = None
                position_mae_time = None
                position_mfe_price = None
                position_mae_price = None
                last_reduce_signature = None
                if sell_center_key is not None:
                    consumed_sell_center_keys.add(sell_center_key)

    # 最后一个bar补权益
    if bars_main:
        last = bars_main[-1]
        while (
            funding_cursor < len(funding_rates)
            and funding_rates[funding_cursor].time <= last.time
        ):
            funding = funding_rates[funding_cursor]
            if (
                position_qty != 0
                and position_entry_time is not None
                and funding.time > position_entry_time
            ):
                mark_price = (
                    funding.mark_price
                    if funding.mark_price > 0
                    else last.close
                )
                funding_pnl = -position_qty * mark_price * funding.rate
                cash += funding_pnl
                position_funding_pnl += funding_pnl
            funding_cursor += 1

        if (
            position_qty != 0
            and position_entry_price > 0
            and position_entry_time is not None
            and last.time > position_entry_time
        ):
            favorable, adverse, favorable_price, adverse_price = (
                _bar_excursions(position_qty, position_entry_price, last)
            )
            if favorable > position_mfe:
                position_mfe = favorable
                position_mfe_time = last.time
                position_mfe_price = favorable_price
            if adverse < position_mae:
                position_mae = adverse
                position_mae_time = last.time
                position_mae_price = adverse_price

        if config.liquidate_at_end and position_qty != 0:
            is_long = position_qty > 0
            qty_to_close = abs(position_qty)
            if is_long:
                exit_price = last.close * (1 - config.slippage_rate)
                proceeds = qty_to_close * exit_price
                exit_fee = proceeds * config.fee_rate
                cash += proceeds - exit_fee
                gross_pnl = (
                    exit_price - position_entry_price
                ) * qty_to_close
                side = "long"
            else:
                exit_price = last.close * (1 + config.slippage_rate)
                cover_cost = qty_to_close * exit_price
                exit_fee = cover_cost * config.fee_rate
                cash -= cover_cost + exit_fee
                gross_pnl = (
                    position_entry_price - exit_price
                ) * qty_to_close
                side = "short"

            net_pnl = (
                gross_pnl
                - position_entry_fee
                - exit_fee
                + position_funding_pnl
            )
            notional = position_entry_price * qty_to_close
            net_return = net_pnl / notional if notional > 0 else 0.0
            if (
                position_signal_index >= 0
                and position_signal_index + 3 < len(bars_main)
            ):
                entry_idx = position_signal_index + 1
                exit_idx = position_signal_index + 3
                fwd_entry = bars_main[entry_idx].open
                forward_long = (
                    (bars_main[exit_idx].close - fwd_entry) / fwd_entry
                    if fwd_entry > 0
                    else 0.0
                )
                forward_return = forward_long if is_long else -forward_long
            else:
                forward_return = 0.0
            benchmark_long = _pick_benchmark_return(
                trade_benchmark_rng,
                year_returns,
                (position_entry_time or last.time).year,
            )
            trades.append(
                Trade(
                    side=side,  # type: ignore[arg-type]
                    signal_type=position_signal_type,  # type: ignore[arg-type]
                    entry_time=position_entry_time or last.time,
                    exit_time=last.time,
                    entry_price=position_entry_price,
                    exit_price=exit_price,
                    quantity=qty_to_close,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    net_return=net_return,
                    fees=position_entry_fee + exit_fee,
                    slippage_cost=(
                        position_entry_slippage
                        + qty_to_close * last.close * config.slippage_rate
                    ),
                    forward_3bar_return=forward_return,
                    benchmark_return=(
                        benchmark_long if is_long else -benchmark_long
                    ),
                    funding_pnl=position_funding_pnl,
                    exit_reason="end_of_test",
                    signal_event_time=position_signal_event_time,
                    signal_available_time=position_signal_available_time,
                    invalid_price=position_stop_price,
                    max_favorable_excursion=position_mfe,
                    max_adverse_excursion=position_mae,
                    mfe_time=position_mfe_time,
                    mae_time=position_mae_time,
                    mfe_price=position_mfe_price,
                    mae_price=position_mae_price,
                )
            )
            position_qty = 0.0
            position_entry_price = 0.0
            position_entry_fee = 0.0
            position_entry_slippage = 0.0
            position_funding_pnl = 0.0
            position_entry_time = None
            position_signal_index = -1
            position_stop_price = None
            position_signal_event_time = None
            position_signal_available_time = None
            position_mfe = 0.0
            position_mae = 0.0
            position_mfe_time = None
            position_mae_time = None
            position_mfe_price = None
            position_mae_price = None

        position_value = position_qty * last.close
        equity = cash + position_value
        if equity > peak_equity:
            peak_equity = equity
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        equity_curve.append(
            EquityPoint(time=last.time, equity=equity, drawdown=drawdown, cash=cash, position_value=position_value)
        )

    significance = evaluate_paired_returns(
        observed=entry_signal_forward_returns,
        baseline=entry_signal_benchmark_returns,
        benchmark=config.benchmark,
        random_seed=config.random_seed,
    )

    metrics = calc_metrics(equity_curve=equity_curve, trades=trades, initial_capital=config.initial_capital)
    segmented_metrics = calc_segmented_metrics(equity_curve=equity_curve, trades=trades, initial_capital=config.initial_capital)
    walk_forward_metrics = calc_walk_forward_metrics(equity_curve=equity_curve, trades=trades, initial_capital=config.initial_capital)
    trade_diagnostics = calc_trade_diagnostics(
        equity_curve=equity_curve,
        trades=trades,
    )

    entry_types = buy_entry_types | (
        sell_entry_types if config.allow_short_entries else set()
    )
    entry_label = "/".join(sorted(entry_types)) if entry_types else "entry"

    sample_count = len(entry_signal_forward_returns)
    entry_expectation = (
        mean(entry_signal_forward_returns) if entry_signal_forward_returns else 0.0
    )

    signal_repaint_rate = repaint_count / repaint_checks if repaint_checks > 0 else 0.0

    pass_checks = {
        "sample_count_ge_80": sample_count >= 80,
        "entry_expectation_gt_0": entry_expectation > 0,
        "p_value_lt_0_05": significance.p_value < 0.05,
        "max_drawdown_le_0_25": metrics.get("max_drawdown", 1.0) <= 0.25,
        "signal_repaint_rate_eq_0": signal_repaint_rate == 0.0,
    }

    fail_reasons = [
        reason
        for key, reason in {
            "sample_count_ge_80": f"有效{entry_label}样本不足80",
            "entry_expectation_gt_0": (
                f"{entry_label}三根主级别有符号前瞻收益期望未大于0"
            ),
            "p_value_lt_0_05": "相对同年份随机三根K线基线未达到统计显著(p>=0.05)",
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
        trade_diagnostics=trade_diagnostics,
        trades=trades,
        signals=decisions_out,
        equity_curve=equity_curve,
    )


def run_cost_scenarios(
    config: BacktestConfig,
    bars_main: list[Bar],
    bars_sub: list[Bar],
    bars_structure: list[Bar] | None = None,
    funding_rates: list[FundingRate] | None = None,
) -> dict[str, BacktestReport]:
    scenarios = {
        "base": config,
        "stress_1": replace(config, fee_rate=0.0015, slippage_rate=0.0005),
        "stress_2": replace(config, fee_rate=0.0020, slippage_rate=0.0010),
    }
    return {
        name: run_backtest(
            cfg,
            bars_main=bars_main,
            bars_sub=bars_sub,
            bars_structure=bars_structure,
            funding_rates=funding_rates,
        )
        for name, cfg in scenarios.items()
    }


def run_sensitivity(
    config: BacktestConfig,
    bars_main: list[Bar],
    bars_sub: list[Bar],
    bars_structure: list[Bar] | None = None,
    funding_rates: list[FundingRate] | None = None,
) -> dict[str, BacktestReport]:
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
            reports[key] = run_backtest(
                cfg,
                bars_main=bars_main,
                bars_sub=bars_sub,
                bars_structure=bars_structure,
                funding_rates=funding_rates,
            )

    return reports
