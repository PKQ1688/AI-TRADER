from __future__ import annotations

from collections.abc import Iterable
from statistics import mean, median, pstdev

from ai_trader.types import EquityPoint, Trade


def _safe_mean(values: Iterable[float]) -> float:
    data = list(values)
    if not data:
        return 0.0
    return mean(data)


def _max_drawdown_from_equity(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _periods_per_year(equity_curve: list[EquityPoint]) -> int:
    intervals = [
        (equity_curve[idx].time - equity_curve[idx - 1].time).total_seconds()
        for idx in range(1, len(equity_curve))
        if equity_curve[idx].time > equity_curve[idx - 1].time
    ]
    if not intervals:
        return 365
    typical_seconds = median(intervals)
    if typical_seconds <= 0:
        return 365
    return max(1, round((365 * 24 * 60 * 60) / typical_seconds))


def _sharpe_from_returns(returns: list[float], periods_per_year: int) -> float:
    if len(returns) < 2:
        return 0.0
    avg = _safe_mean(returns)
    std = pstdev(returns)
    if std == 0:
        return 0.0
    return (avg / std) * (periods_per_year**0.5)


def _position_outcomes(trades: list[Trade]) -> list[tuple[float, float]]:
    """Aggregate partial exit fills into one outcome per entry position."""
    grouped: dict[tuple, list[float]] = {}
    for trade in trades:
        key = (
            trade.side,
            trade.signal_type,
            trade.entry_time,
            trade.entry_price,
        )
        state = grouped.setdefault(key, [0.0, 0.0])
        state[0] += trade.net_pnl
        state[1] += trade.entry_price * trade.quantity

    outcomes: list[tuple[float, float]] = []
    for net_pnl, closed_notional in grouped.values():
        net_return = net_pnl / closed_notional if closed_notional > 0 else 0.0
        outcomes.append((net_pnl, net_return))
    return outcomes


def calc_trade_diagnostics(
    equity_curve: list[EquityPoint],
    trades: list[Trade],
) -> dict[str, float]:
    """Summarize turnover, exposure, confirmation lag, and PnL concentration."""
    grouped: dict[tuple, dict[str, object]] = {}
    for trade in trades:
        key = (
            trade.side,
            trade.signal_type,
            trade.entry_time,
            trade.entry_price,
        )
        state = grouped.setdefault(
            key,
            {
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "net_pnl": 0.0,
                "end_of_test": False,
                "confirmation_lag_hours": None,
            },
        )
        state["exit_time"] = max(state["exit_time"], trade.exit_time)
        state["net_pnl"] = float(state["net_pnl"]) + trade.net_pnl
        state["end_of_test"] = bool(state["end_of_test"]) or (
            trade.exit_reason == "end_of_test"
        )
        if (
            trade.signal_event_time is not None
            and trade.signal_available_time is not None
        ):
            state["confirmation_lag_hours"] = (
                trade.signal_available_time - trade.signal_event_time
            ).total_seconds() / 3600

    positions = list(grouped.values())
    evaluation_hours = 0.0
    if len(equity_curve) >= 2:
        evaluation_hours = (
            equity_curve[-1].time - equity_curve[0].time
        ).total_seconds() / 3600

    holding_hours = [
        (state["exit_time"] - state["entry_time"]).total_seconds() / 3600
        for state in positions
    ]
    confirmation_lags = [
        float(state["confirmation_lag_hours"])
        for state in positions
        if state["confirmation_lag_hours"] is not None
    ]
    total_net_pnl = sum(float(state["net_pnl"]) for state in positions)
    largest_winner = max(
        (float(state["net_pnl"]) for state in positions),
        default=0.0,
    )
    end_of_test_pnl = sum(
        float(state["net_pnl"])
        for state in positions
        if bool(state["end_of_test"])
    )
    evaluation_days = evaluation_hours / 24

    return {
        "evaluation_days": evaluation_days,
        "positions_per_30_days": (
            len(positions) * 30 / evaluation_days
            if evaluation_days > 0
            else 0.0
        ),
        "time_in_market": (
            min(1.0, sum(holding_hours) / evaluation_hours)
            if evaluation_hours > 0
            else 0.0
        ),
        "average_holding_days": (
            mean(holding_hours) / 24 if holding_hours else 0.0
        ),
        "median_holding_days": (
            median(holding_hours) / 24 if holding_hours else 0.0
        ),
        "average_confirmation_lag_hours": (
            mean(confirmation_lags) if confirmation_lags else 0.0
        ),
        "max_confirmation_lag_hours": (
            max(confirmation_lags) if confirmation_lags else 0.0
        ),
        "end_of_test_position_count": float(
            sum(bool(state["end_of_test"]) for state in positions)
        ),
        "end_of_test_pnl_share": (
            end_of_test_pnl / total_net_pnl
            if total_net_pnl != 0
            else 0.0
        ),
        "largest_winner_share_of_net_pnl": (
            largest_winner / total_net_pnl
            if total_net_pnl > 0 and largest_winner > 0
            else 0.0
        ),
    }


def calc_metrics(equity_curve: list[EquityPoint], trades: list[Trade], initial_capital: float) -> dict[str, float]:
    equity = [item.equity for item in equity_curve]
    if not equity:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "trade_count": 0.0,
            "fill_count": 0.0,
        }

    total_return = (equity[-1] - initial_capital) / initial_capital

    if len(equity_curve) >= 2:
        days = (equity_curve[-1].time - equity_curve[0].time).total_seconds() / 86400
    else:
        days = 0.0
    annual_return = 0.0
    if days > 0:
        growth_multiple = equity[-1] / initial_capital
        annual_return = (
            growth_multiple ** (365 / days) - 1
            if growth_multiple > 0
            else -1.0
        )

    returns = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        if prev <= 0:
            returns.append(0.0)
        else:
            returns.append((equity[i] - prev) / prev)

    position_outcomes = _position_outcomes(trades)
    gross_profit = sum(pnl for pnl, _ in position_outcomes if pnl > 0)
    gross_loss = abs(sum(pnl for pnl, _ in position_outcomes if pnl < 0))
    win_count = sum(1 for pnl, _ in position_outcomes if pnl > 0)

    periods_per_year = _periods_per_year(equity_curve)
    metrics = {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": _max_drawdown_from_equity(equity),
        "sharpe": _sharpe_from_returns(returns, periods_per_year),
        "win_rate": (
            win_count / len(position_outcomes) if position_outcomes else 0.0
        ),
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else 0.0,
        "expectancy": _safe_mean([item[1] for item in position_outcomes]),
        "trade_count": float(len(position_outcomes)),
        "fill_count": float(len(trades)),
    }

    return metrics


def calc_segmented_metrics(equity_curve: list[EquityPoint], trades: list[Trade], initial_capital: float) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    years = sorted({item.time.year for item in equity_curve})
    for year in years:
        segment_equity = [item for item in equity_curve if item.time.year == year]
        segment_trades = [item for item in trades if item.entry_time.year == year]
        cap = initial_capital if not segment_equity else segment_equity[0].equity
        result[str(year)] = calc_metrics(segment_equity, segment_trades, cap)

    return result


def calc_walk_forward_metrics(equity_curve: list[EquityPoint], trades: list[Trade], initial_capital: float) -> dict[str, dict[str, float]]:
    if len(equity_curve) < 2:
        return {
            "train_first_70pct": calc_metrics(
                equity_curve,
                trades,
                initial_capital,
            ),
            "validate_last_30pct": calc_metrics([], [], initial_capital),
        }

    split_idx = min(
        len(equity_curve) - 1,
        max(1, int(len(equity_curve) * 0.70)),
    )
    cutoff = equity_curve[split_idx].time
    train_equity = equity_curve[: split_idx + 1]
    val_equity = equity_curve[split_idx:]
    train_trades = [item for item in trades if item.entry_time < cutoff]
    val_trades = [item for item in trades if item.entry_time >= cutoff]

    return {
        "train_first_70pct": calc_metrics(
            train_equity,
            train_trades,
            initial_capital,
        ),
        "validate_last_30pct": calc_metrics(
            val_equity,
            val_trades,
            (val_equity[0].equity if val_equity else initial_capital),
        ),
    }
