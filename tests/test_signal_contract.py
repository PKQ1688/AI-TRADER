from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ai_trader.chan import build_chan_state, generate_signal
from tests.test_utils import make_synthetic_bars


class SignalContractTest(unittest.TestCase):
    def test_contract_contains_extended_market_state_fields(self) -> None:
        start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=300, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=1200, step_hours=1)

        asof = bars_main[240].time
        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=asof,
        )
        payload = generate_signal(snapshot).to_contract_dict()

        market_state = payload["market_state"]
        for key in [
            "trend_type",
            "walk_type",
            "phase",
            "zhongshu_count",
            "last_zhongshu",
            "current_stroke_dir",
            "current_segment_dir",
        ]:
            self.assertIn(key, market_state)

        self.assertIn(market_state["trend_type"], {"up", "down", "range"})
        self.assertIn(market_state["walk_type"], {"consolidation", "trend"})
        self.assertIn(market_state["phase"], {"trending", "consolidating", "transitional"})
        self.assertGreaterEqual(market_state["zhongshu_count"], 0)

        last_zs = market_state["last_zhongshu"]
        for key in ["zd", "zg", "gg", "dd"]:
            self.assertIn(key, last_zs)

        self.assertIn(payload["action"]["decision"], {"buy", "sell", "reduce", "hold", "wait"})
        self.assertIn(payload["risk"]["conflict_level"], {"none", "low", "high"})

        for sig in payload["signals"]:
            self.assertIn(sig["type"], {"B1", "B2", "B3", "S1", "S2", "S3"})
            self.assertIn(sig["level"], {"main", "sub"})
            self.assertGreaterEqual(sig["confidence"], 0.0)
            self.assertLessEqual(sig["confidence"], 1.0)
            self.assertTrue(sig["trigger"])
            self.assertTrue(sig["invalid_if"])


if __name__ == "__main__":
    unittest.main()
