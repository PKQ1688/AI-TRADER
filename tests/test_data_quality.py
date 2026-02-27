from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ai_trader.chan import build_chan_state, generate_signal
from tests.test_utils import make_synthetic_bars


class DataQualityTest(unittest.TestCase):
    def test_missing_sub_bars_returns_insufficient(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=80, step_hours=4)
        bars_sub: list = []

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
        )
        decision = generate_signal(snapshot)
        payload = decision.to_contract_dict()

        self.assertEqual(payload["data_quality"]["status"], "insufficient")
        self.assertEqual(payload["action"]["decision"], "wait")


if __name__ == "__main__":
    unittest.main()
