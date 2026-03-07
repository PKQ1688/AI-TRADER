from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ai_trader.chan.config import get_chan_config
from ai_trader.chan.core.include import merge_inclusions_with_trace
from ai_trader.chan.review import build_review_snapshot
from ai_trader.types import Bar, iso_utc
from tests.test_utils import make_synthetic_bars


class ChanReviewTest(unittest.TestCase):
    def _t(self, i: int, hours: int = 4) -> datetime:
        return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i * hours)

    def test_merge_inclusions_with_trace_tracks_raw_indices(self) -> None:
        bars = [
            Bar(time=self._t(0), open=8, high=10, low=5, close=9),
            Bar(time=self._t(1), open=9, high=12, low=6, close=11),
            Bar(time=self._t(2), open=11, high=11, low=7, close=8),
            Bar(time=self._t(3), open=8, high=13, low=8, close=12),
            Bar(time=self._t(4), open=12, high=12, low=9, close=10),
        ]

        merged, traces = merge_inclusions_with_trace(bars)

        self.assertEqual(len(merged), 3)
        self.assertEqual([item.raw_indices for item in traces], [[0], [1, 2], [3, 4]])
        self.assertEqual(traces[1].direction, 1)
        self.assertEqual(traces[2].direction, 1)

    def test_build_review_snapshot_respects_windows_and_asof(self) -> None:
        bars_main = make_synthetic_bars(start=self._t(0), count=90, step_hours=4)
        bars_sub = make_synthetic_bars(
            start=self._t(0, hours=1),
            count=220,
            step_hours=1,
            drift=1.2,
            wave_amp=80.0,
        )
        asof = bars_main[79].time

        snapshot = build_review_snapshot(
            bars_main=bars_main,
            bars_sub=bars_sub,
            asof_time=asof,
            exchange="binance",
            symbol="BTC/USDT",
            timeframe_main="4h",
            timeframe_sub="1h",
            start=iso_utc(bars_main[0].time),
            end=iso_utc(bars_main[-1].time),
            window_main=30,
            window_sub=50,
            chan_config=get_chan_config("pragmatic"),
        )

        self.assertEqual(snapshot["meta"]["asof"], iso_utc(asof))
        self.assertEqual(snapshot["summary"]["raw_main_count"], 80)
        self.assertLessEqual(len(snapshot["main"]["raw_bars"]), 30)
        self.assertLessEqual(len(snapshot["sub"]["raw_bars"]), 50)
        self.assertEqual(snapshot["decision"]["data_quality"]["status"], "ok")
        self.assertEqual(snapshot["main"]["merged_bars"][-1]["raw_end_index"], 79)
        self.assertIsInstance(snapshot["signals_full"], list)


if __name__ == "__main__":
    unittest.main()
