from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ai_trader.backtest.significance import (
    evaluate_paired_returns,
    evaluate_significance,
)
from ai_trader.types import Trade


class BacktestSignificanceTest(unittest.TestCase):
    def _trade(self, idx: int, observed: float, baseline: float) -> Trade:
        entry = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=idx)
        return Trade(
            side="long",
            signal_type="B3",
            entry_time=entry,
            exit_time=entry + timedelta(hours=1),
            entry_price=100.0,
            exit_price=101.0,
            quantity=1.0,
            gross_pnl=1.0,
            net_pnl=1.0,
            net_return=0.01,
            fees=0.0,
            slippage_cost=0.0,
            forward_3bar_return=observed,
            benchmark_return=baseline,
        )

    def test_block_bootstrap_preserves_matched_pairs(self) -> None:
        trades = [
            self._trade(0, -0.20, -0.30),
            self._trade(1, 0.00, -0.10),
            self._trade(2, 0.25, 0.15),
            self._trade(3, 0.80, 0.70),
        ]

        result = evaluate_significance(
            trades,
            bootstrap_rounds=500,
            random_seed=11,
        )

        self.assertAlmostEqual(result.mean_diff, 0.10)
        self.assertAlmostEqual(result.ci_low, 0.10)
        self.assertAlmostEqual(result.ci_high, 0.10)
        self.assertGreater(result.p_value, 0.0)
        self.assertLess(result.p_value, 0.5)
        self.assertEqual(result.block_size, 2)
        self.assertIn("block", result.test_method)

    def test_paired_return_lengths_must_match(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_paired_returns([0.1], [])


if __name__ == "__main__":
    unittest.main()
