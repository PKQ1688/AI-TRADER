from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ai_trader.backtest.engine import run_backtest
from ai_trader.types import BacktestConfig
from tests.test_utils import make_synthetic_bars


class RepaintRateTest(unittest.TestCase):
    def test_signal_repaint_rate_is_zero(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=260, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=1040, step_hours=1)

        report = run_backtest(config=BacktestConfig(), bars_main=bars_main, bars_sub=bars_sub)
        self.assertEqual(report.signal_repaint_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
