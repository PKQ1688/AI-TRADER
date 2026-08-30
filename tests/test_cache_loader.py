from __future__ import annotations

import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from ai_trader.data import cache_path_for, load_ohlcv
from ai_trader.data.binance_ohlcv import _fetch_with_binance_rest_ms
from tests.test_utils import make_synthetic_bars


class CacheLoaderTest(unittest.TestCase):
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

    def _write_cache(self, exchange: str, symbol: str, timeframe: str, bars) -> Path:
        path = cache_path_for(exchange, symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            for bar in bars:
                writer.writerow(bar.to_dict())
        return path

    def test_full_cache_returns_without_network(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = make_synthetic_bars(start=start, count=20, step_hours=4)
        self._write_cache("unknown_exchange", "BTC/USDT", "4h", bars)
        query_start = bars[0].time + timedelta(hours=4)
        query_end = bars[-1].time + timedelta(hours=4)

        result = load_ohlcv(
            exchange="unknown_exchange",
            symbol="BTC/USDT",
            timeframe="4h",
            start_utc=query_start.isoformat().replace("+00:00", "Z"),
            end_utc=query_end.isoformat().replace("+00:00", "Z"),
        )
        self.assertEqual(len(result), len(bars))
        self.assertEqual(result[0].time, query_start)
        self.assertEqual(result[-1].time, query_end)

    def test_incomplete_cache_triggers_fetch_and_fails_for_unknown_exchange(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = make_synthetic_bars(start=start, count=20, step_hours=4)
        self._write_cache("unknown_exchange", "BTC/USDT", "4h", bars[:10])
        query_start = bars[0].time + timedelta(hours=4)
        query_end = bars[-1].time + timedelta(hours=4)

        with self.assertRaises(ValueError):
            load_ohlcv(
                exchange="unknown_exchange",
                symbol="BTC/USDT",
                timeframe="4h",
                start_utc=query_start.isoformat().replace("+00:00", "Z"),
                end_utc=query_end.isoformat().replace("+00:00", "Z"),
            )

    def test_usdm_market_uses_futures_rest_endpoint(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            [
                1_700_000_000_000,
                "100.0",
                "102.0",
                "99.0",
                "101.0",
                "12.5",
            ]
        ]

        with patch("requests.get", return_value=response) as request_get:
            bars = _fetch_with_binance_rest_ms(
                symbol="BTC/USDT",
                timeframe="1m",
                start_ms=1_700_000_000_000,
                end_ms=1_700_000_000_000,
                market="usdm",
            )

        self.assertEqual(len(bars), 1)
        self.assertEqual(
            request_get.call_args.args[0],
            "https://fapi.binance.com/fapi/v1/klines",
        )

    def test_spot_and_usdm_caches_are_separate(self) -> None:
        spot = cache_path_for("binance", "BTC/USDT", "1m")
        perpetual = cache_path_for("binanceusdm", "BTC/USDT", "1m")

        self.assertNotEqual(spot, perpetual)
        self.assertIn("binanceusdm", perpetual.parts)


if __name__ == "__main__":
    unittest.main()
