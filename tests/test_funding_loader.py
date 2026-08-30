from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from ai_trader.data import funding_cache_path_for, load_funding_rates
from ai_trader.data.binance_funding import _fetch_funding_range
from ai_trader.types import FundingRate


class FundingLoaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data_dir = os.environ.get("AI_TRADER_DATA_DIR")
        os.environ["AI_TRADER_DATA_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._old_data_dir is None:
            os.environ.pop("AI_TRADER_DATA_DIR", None)
        else:
            os.environ["AI_TRADER_DATA_DIR"] = self._old_data_dir
        self._tmp.cleanup()

    def test_complete_funding_history_is_cached_without_refetch(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        rates = [
            FundingRate(
                time=start + timedelta(hours=offset),
                rate=0.0001,
                mark_price=100_000.0,
            )
            for offset in (0, 8, 16)
        ]

        with patch(
            "ai_trader.data.binance_funding._fetch_with_retry",
            return_value=rates,
        ) as fetch:
            result = load_funding_rates(
                "binanceusdm",
                "BTC/USDT",
                start.isoformat(),
                (start + timedelta(hours=16)).isoformat(),
            )

        self.assertEqual(result, rates)
        fetch.assert_called_once()
        self.assertTrue(
            funding_cache_path_for("binanceusdm", "BTC/USDT").exists()
        )

        with patch(
            "ai_trader.data.binance_funding._fetch_with_retry"
        ) as second_fetch:
            cached = load_funding_rates(
                "binanceusdm",
                "BTC/USDT",
                start.isoformat(),
                (start + timedelta(hours=16)).isoformat(),
            )

        self.assertEqual(cached, rates)
        second_fetch.assert_not_called()

    def test_missing_scheduled_funding_rate_is_not_assumed_zero(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        with patch(
            "ai_trader.data.binance_funding._fetch_with_retry",
            return_value=[],
        ):
            with self.assertRaisesRegex(RuntimeError, "no funding records"):
                load_funding_rates(
                    "binanceusdm",
                    "BTC/USDT",
                    start.isoformat(),
                    (start + timedelta(hours=8)).isoformat(),
                )

    def test_funding_history_uses_futures_endpoint(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "symbol": "BTCUSDT",
                "fundingTime": 1_735_689_600_000,
                "fundingRate": "0.00010000",
                "markPrice": "93425.10000000",
            }
        ]

        with patch("requests.get", return_value=response) as request_get:
            result = _fetch_funding_range(
                "BTC/USDT",
                1_735_689_600_000,
                1_735_689_600_000,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].rate, 0.0001)
        self.assertEqual(
            request_get.call_args.args[0],
            "https://fapi.binance.com/fapi/v1/fundingRate",
        )


if __name__ == "__main__":
    unittest.main()
