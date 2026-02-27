from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ai_trader.chan import build_chan_state, generate_signal
from tests.test_utils import make_synthetic_bars


class TemporalConsistencyTest(unittest.TestCase):
    def test_signal_same_for_same_asof_with_more_future_bars(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=260, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=1040, step_hours=1)

        asof = bars_main[200].time

        snapshot_a = build_chan_state(
            bars_main=bars_main[:201],
            bars_sub=[item for item in bars_sub if item.time <= asof],
            macd_main=None,
            macd_sub=None,
            asof_time=asof,
        )
        snapshot_b = build_chan_state(
            bars_main=bars_main[:240],
            bars_sub=bars_sub[:960],
            macd_main=None,
            macd_sub=None,
            asof_time=asof,
        )

        decision_a = generate_signal(snapshot_a).to_contract_dict()
        decision_b = generate_signal(snapshot_b).to_contract_dict()

        self.assertEqual(decision_a["action"]["decision"], decision_b["action"]["decision"])
        self.assertEqual(decision_a["signals"], decision_b["signals"])


if __name__ == "__main__":
    unittest.main()
