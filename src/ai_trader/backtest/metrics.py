from __future__ import annotations

from collections.abc import Iterable
from statistics import mean, pstdev

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


def _sharpe_from_returns(returns: list[float], periods_per_year: int = 365 * 6) -> float:
    if len(returns) < 2:
        return 0.0
    avg = _safe_mean(returns)
    std = pstdev(returns)
    if std == 0:
        return 0.0
    return (avg / std) * (periods_per_year**0.5)


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
        }

    total_return = (equity[-1] - initial_capital) / initial_capital

    if len(equity_curve) >= 2:
        days = (equity_curve[-1].time - equity_curve[0].time).total_seconds() / 86400
    else:
        days = 0.0
    annual_return = 0.0
    if days > 0:
        annual_return = (1 + total_return) ** (365 / days) - 1

    returns = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        if prev <= 0:
            returns.append(0.0)
        else:
            returns.append((equity[i] - prev) / prev)

    gross_profit = sum(item.net_pnl for item in trades if item.net_pnl > 0)
    gross_loss = abs(sum(item.net_pnl for item in trades if item.net_pnl < 0))
    win_count = sum(1 for item in trades if item.net_pnl > 0)

    metrics = {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": _max_drawdown_from_equity(equity),
        "sharpe": _sharpe_from_returns(returns),
        "win_rate": (win_count / len(trades)) if trades else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else 0.0,
        "expectancy": _safe_mean([item.net_return for item in trades]),
        "trade_count": float(len(trades)),
    }

    try:
        import empyrical as ep  # type: ignore

        series = returns if returns else [0.0]
        metrics["max_drawdown"] = float(ep.max_drawdown(series)) * -1.0
        metrics["sharpe"] = float(ep.sharpe_ratio(series) or 0.0)
    except Exception:
        pass

    return metrics


def calc_segmented_metrics(equity_curve: list[EquityPoint], trades: list[Trade], initial_capital: float) -> dict[str, dict[str, float]]:
    segments = {
        "2022": (2022, 2022),
        "2023": (2023, 2023),
        "2024-2026": (2024, 2026),
    }

    result: dict[str, dict[str, float]] = {}
    for name, (start_year, end_year) in segments.items():
        segment_equity = [item for item in equity_curve if start_year <= item.time.year <= end_year]
        segment_trades = [item for item in trades if start_year <= item.entry_time.year <= end_year]
        cap = initial_capital if not segment_equity else segment_equity[0].equity
        result[name] = calc_metrics(segment_equity, segment_trades, cap)

    return result


def calc_walk_forward_metrics(equity_curve: list[EquityPoint], trades: list[Trade], initial_capital: float) -> dict[str, dict[str, float]]:
    train_equity = [item for item in equity_curve if 2022 <= item.time.year <= 2023]
    val_equity = [item for item in equity_curve if 2024 <= item.time.year <= 2026]

    train_trades = [item for item in trades if 2022 <= item.entry_time.year <= 2023]
    val_trades = [item for item in trades if 2024 <= item.entry_time.year <= 2026]

    return {
        "train_2022_2023": calc_metrics(train_equity, train_trades, initial_capital),
        "validate_2024_2026": calc_metrics(
            val_equity,
            val_trades,
            (val_equity[0].equity if val_equity else initial_capital),
        ),
    }
