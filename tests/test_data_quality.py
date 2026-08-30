from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ai_trader.chan import build_chan_state, generate_signal
from ai_trader.types import Bar
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

    def test_gap_in_continuous_crypto_bars_returns_insufficient(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=80, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=320, step_hours=1)
        bars_sub[160].time += timedelta(hours=1)

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
        )

        self.assertEqual(snapshot.data_quality.status, "insufficient")
        self.assertIn("K线不连续", snapshot.data_quality.notes)

    def test_duplicate_timestamp_returns_insufficient(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=80, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=320, step_hours=1)
        bars_main[40].time = bars_main[39].time

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
        )

        self.assertEqual(snapshot.data_quality.status, "insufficient")
        self.assertIn("时间戳重复", snapshot.data_quality.notes)

    def test_invalid_ohlc_range_returns_insufficient(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=80, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=320, step_hours=1)
        original = bars_main[40]
        bars_main[40] = Bar(
            time=original.time,
            open=original.open,
            high=original.open - 1.0,
            low=original.low,
            close=original.close,
            volume=original.volume,
        )

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
        )

        self.assertEqual(snapshot.data_quality.status, "insufficient")
        self.assertIn("high 低于", snapshot.data_quality.notes)

    def test_sub_bars_must_cover_latest_completed_main_bar(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=80, step_hours=4)
        bars_sub = make_synthetic_bars(start=start, count=316, step_hours=1)

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
        )

        self.assertEqual(snapshot.data_quality.status, "insufficient")
        self.assertIn("bars_sub 未覆盖", snapshot.data_quality.notes)

    def test_sub_timeframe_must_be_finer_and_divide_main_timeframe(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars_main = make_synthetic_bars(start=start, count=320, step_hours=1)
        bars_sub = make_synthetic_bars(start=start, count=80, step_hours=4)

        snapshot = build_chan_state(
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=None,
            macd_sub=None,
            asof_time=bars_main[-1].time,
            timeframe_main="1h",
            timeframe_sub="4h",
        )

        self.assertEqual(snapshot.data_quality.status, "insufficient")
        self.assertIn("timeframe_sub 必须严格小于", snapshot.data_quality.notes)


if __name__ == "__main__":
    unittest.main()
