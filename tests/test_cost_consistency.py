from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from ai_trader.backtest.engine import run_backtest
from ai_trader.types import BacktestConfig
from tests.test_utils import make_synthetic_bars


class CostConsistencyTest(unittest.TestCase):
    def test_costed_result_not_better_than_zero_cost(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=260, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=1040, step_hours=1)

        base = BacktestConfig(fee_rate=0.0, slippage_rate=0.0)
        costed = replace(base, fee_rate=0.001, slippage_rate=0.0002)

        no_cost_report = run_backtest(config=base, bars_main=bars_main, bars_sub=bars_sub)
        costed_report = run_backtest(config=costed, bars_main=bars_main, bars_sub=bars_sub)

        self.assertLessEqual(costed_report.metrics["total_return"], no_cost_report.metrics["total_return"] + 1e-12)


if __name__ == "__main__":
    unittest.main()
