from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ai_trader.chan import build_chan_state, generate_signal
from tests.test_utils import make_synthetic_bars


class RecursiveChanStructureTest(unittest.TestCase):
    def test_build_chan_state_exposes_recursive_levels(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(
            start=start,
            count=360,
            step_hours=4,
            drift=2.0,
            wave_amp=320.0,
        )
        bars_sub = make_synthetic_bars(
            start=start,
            count=1440,
            step_hours=1,
            drift=0.6,
            wave_amp=90.0,
        )

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
        )

        self.assertTrue(snapshot.structure_levels_main)
        base = snapshot.structure_levels_main[0]
        self.assertEqual(base.level, 0)
        self.assertTrue(base.units)
        self.assertTrue(base.centers or base.walks)

        payload = generate_signal(snapshot).to_contract_dict()
        market_state = payload["market_state"]
        self.assertIn("current_walk", market_state)
        self.assertIn("level_states", market_state)
        self.assertTrue(market_state["level_states"])
        self.assertEqual(market_state["level_states"][0]["level"], 0)


if __name__ == "__main__":
    unittest.main()
