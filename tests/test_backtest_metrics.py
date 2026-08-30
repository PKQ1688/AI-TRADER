from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev

from ai_trader.backtest.metrics import (
    calc_metrics,
    calc_segmented_metrics,
    calc_trade_diagnostics,
    calc_walk_forward_metrics,
)
from ai_trader.types import EquityPoint, Trade


class BacktestMetricsTest(unittest.TestCase):
    def test_sharpe_annualization_uses_actual_30m_interval(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        returns = [0.01, -0.005, 0.002, 0.008]
        values = [100.0]
        for item in returns:
            values.append(values[-1] * (1 + item))

        curve = [
            EquityPoint(
                time=start + timedelta(minutes=30 * idx),
                equity=value,
                drawdown=0.0,
                cash=value,
                position_value=0.0,
            )
            for idx, value in enumerate(values)
        ]

        metrics = calc_metrics(curve, trades=[], initial_capital=100.0)
        expected = (mean(returns) / pstdev(returns)) * ((365 * 48) ** 0.5)

        self.assertAlmostEqual(metrics["sharpe"], expected)

    def test_partial_exits_count_as_one_position_outcome(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        equity_curve = [
            EquityPoint(
                time=start,
                equity=1000.0,
                drawdown=0.0,
                cash=1000.0,
                position_value=0.0,
            ),
            EquityPoint(
                time=start + timedelta(minutes=30),
                equity=1005.0,
                drawdown=0.0,
                cash=1005.0,
                position_value=0.0,
            ),
        ]
        common = {
            "side": "long",
            "signal_type": "B3",
            "entry_time": start,
            "entry_price": 100.0,
            "quantity": 0.5,
            "fees": 0.0,
            "slippage_cost": 0.0,
            "forward_3bar_return": 0.02,
            "benchmark_return": 0.01,
        }
        trades = [
            Trade(
                **common,
                exit_time=start + timedelta(minutes=15),
                exit_price=106.0,
                gross_pnl=3.0,
                net_pnl=3.0,
                net_return=0.06,
            ),
            Trade(
                **common,
                exit_time=start + timedelta(minutes=30),
                exit_price=104.0,
                gross_pnl=2.0,
                net_pnl=2.0,
                net_return=0.04,
            ),
        ]

        metrics = calc_metrics(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=1000.0,
        )

        self.assertEqual(metrics["trade_count"], 1.0)
        self.assertEqual(metrics["fill_count"], 2.0)
        self.assertEqual(metrics["win_rate"], 1.0)
        self.assertAlmostEqual(metrics["expectancy"], 0.05)

    def test_period_reports_follow_actual_data_dates(self) -> None:
        curve = [
            EquityPoint(
                time=datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc),
                equity=100.0,
                drawdown=0.0,
                cash=100.0,
                position_value=0.0,
            ),
            EquityPoint(
                time=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                equity=101.0,
                drawdown=0.0,
                cash=101.0,
                position_value=0.0,
            ),
            EquityPoint(
                time=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
                equity=102.0,
                drawdown=0.0,
                cash=102.0,
                position_value=0.0,
            ),
            EquityPoint(
                time=datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
                equity=103.0,
                drawdown=0.0,
                cash=103.0,
                position_value=0.0,
            ),
        ]

        segmented = calc_segmented_metrics(curve, [], 100.0)
        walk_forward = calc_walk_forward_metrics(curve, [], 100.0)

        self.assertEqual(set(segmented), {"2025", "2026"})
        self.assertEqual(
            set(walk_forward),
            {"train_first_70pct", "validate_last_30pct"},
        )
        self.assertGreaterEqual(
            walk_forward["validate_last_30pct"]["total_return"],
            0.0,
        )

    def test_non_positive_terminal_equity_has_finite_annual_return(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        curve = [
            EquityPoint(
                time=start,
                equity=100.0,
                drawdown=0.0,
                cash=100.0,
                position_value=0.0,
            ),
            EquityPoint(
                time=start + timedelta(days=30),
                equity=-5.0,
                drawdown=1.05,
                cash=-5.0,
                position_value=0.0,
            ),
        ]

        metrics = calc_metrics(curve, [], initial_capital=100.0)

        self.assertEqual(metrics["annual_return"], -1.0)

    def test_trade_diagnostics_expose_frequency_and_profit_concentration(
        self,
    ) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        curve = [
            EquityPoint(
                time=start,
                equity=100.0,
                drawdown=0.0,
                cash=100.0,
                position_value=0.0,
            ),
            EquityPoint(
                time=start + timedelta(days=60),
                equity=120.0,
                drawdown=0.0,
                cash=120.0,
                position_value=0.0,
            ),
        ]
        common = {
            "side": "long",
            "signal_type": "B3",
            "entry_price": 100.0,
            "exit_price": 100.0,
            "quantity": 1.0,
            "gross_pnl": 0.0,
            "net_return": 0.0,
            "fees": 0.0,
            "slippage_cost": 0.0,
            "forward_3bar_return": 0.0,
            "benchmark_return": 0.0,
        }
        trades = [
            Trade(
                **common,
                entry_time=start,
                exit_time=start + timedelta(days=10),
                net_pnl=-10.0,
                signal_event_time=start - timedelta(hours=24),
                signal_available_time=start,
            ),
            Trade(
                **common,
                entry_time=start + timedelta(days=30),
                exit_time=start + timedelta(days=60),
                net_pnl=30.0,
                exit_reason="end_of_test",
                signal_event_time=start + timedelta(days=28),
                signal_available_time=start + timedelta(days=30),
            ),
        ]

        diagnostics = calc_trade_diagnostics(curve, trades)

        self.assertAlmostEqual(diagnostics["positions_per_30_days"], 1.0)
        self.assertAlmostEqual(diagnostics["time_in_market"], 2 / 3)
        self.assertAlmostEqual(
            diagnostics["largest_winner_share_of_net_pnl"],
            1.5,
        )
        self.assertAlmostEqual(diagnostics["end_of_test_pnl_share"], 1.5)
        self.assertEqual(diagnostics["average_confirmation_lag_hours"], 36.0)


if __name__ == "__main__":
    unittest.main()
