from __future__ import annotations
# ruff: noqa: E402

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone

from _script_utils import ensure_src_on_path

ensure_src_on_path()

from ai_trader.data import cache_path_for
from ai_trader.types import Bar, iso_utc


def _read_raw(exchange: str, symbol: str, timeframe: str) -> list[Bar]:
    path = cache_path_for(exchange, symbol, timeframe)
    bars: list[Bar] = []
    with path.open("r", newline="") as f:
        for row in csv.DictReader(f):
            bars.append(
                Bar(
                    time=row["time"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return sorted(bars, key=lambda item: item.time)


def _cadence_and_ohlc(bars: list[Bar], step_seconds: int) -> dict:
    gap_count = 0
    invalid_ohlc_count = 0
    for previous, current in zip(bars, bars[1:]):
        if int((current.time - previous.time).total_seconds()) != step_seconds:
            gap_count += 1
    for item in bars:
        if (
            item.high < max(item.open, item.close, item.low)
            or item.low > min(item.open, item.close, item.high)
            or min(item.open, item.high, item.low, item.close) <= 0
            or item.volume < 0
        ):
            invalid_ohlc_count += 1
    return {
        "count": len(bars),
        "start": iso_utc(bars[0].time) if bars else None,
        "end": iso_utc(bars[-1].time) if bars else None,
        "gap_count": gap_count,
        "invalid_ohlc_count": invalid_ohlc_count,
    }


def _aggregate_check(
    lower: list[Bar],
    higher: list[Bar],
    ratio: int,
    lower_step_seconds: int,
) -> dict:
    by_time = {item.time: item for item in lower}
    mismatch_count = 0
    incomplete_count = 0
    mismatch_samples = []
    for target in higher:
        source = [
            by_time.get(
                target.time
                + timedelta(seconds=offset * lower_step_seconds),
            )
            for offset in range(ratio)
        ]
        if any(item is None for item in source):
            incomplete_count += 1
            continue
        complete = [item for item in source if item is not None]
        # Binance emits zero-volume minute candles at the last known price,
        # while a higher-timeframe candle opens at the first actual trade.
        # Aggregate price fields from traded sub-bars whenever they exist.
        traded = [item for item in complete if item.volume > 0] or complete
        expected = (
            traded[0].open,
            max(item.high for item in traded),
            min(item.low for item in traded),
            traded[-1].close,
            sum(item.volume for item in complete),
        )
        actual = (
            target.open,
            target.high,
            target.low,
            target.close,
            target.volume,
        )
        price_mismatch = any(
            abs(expected[idx] - actual[idx]) > 1e-8 for idx in range(4)
        )
        volume_mismatch = abs(expected[4] - actual[4]) > 1e-6
        if price_mismatch or volume_mismatch:
            mismatch_count += 1
            if len(mismatch_samples) < 5:
                mismatch_samples.append(
                    {
                        "time": iso_utc(target.time),
                        "expected": expected,
                        "actual": actual,
                    }
                )
    return {
        "checked": len(higher) - incomplete_count,
        "incomplete_count": incomplete_count,
        "mismatch_count": mismatch_count,
        "mismatch_samples": mismatch_samples,
    }


def audit(exchange: str, symbol: str) -> dict:
    bars_1m = _read_raw(exchange, symbol, "1m")
    bars_5m = _read_raw(exchange, symbol, "5m")
    bars_30m = _read_raw(exchange, symbol, "30m")
    result = {
        "exchange": exchange,
        "symbol": symbol,
        "generated_at": iso_utc(datetime.now(timezone.utc)),
        "timeframes": {
            "1m": _cadence_and_ohlc(bars_1m, 60),
            "5m": _cadence_and_ohlc(bars_5m, 5 * 60),
            "30m": _cadence_and_ohlc(bars_30m, 30 * 60),
        },
        "aggregation": {
            "1m_to_5m": _aggregate_check(bars_1m, bars_5m, 5, 60),
            "5m_to_30m": _aggregate_check(bars_5m, bars_30m, 6, 5 * 60),
        },
    }
    failures = []
    for timeframe, stats in result["timeframes"].items():
        if stats["gap_count"] or stats["invalid_ohlc_count"]:
            failures.append(timeframe)
    for relation, stats in result["aggregation"].items():
        if stats["incomplete_count"] or stats["mismatch_count"]:
            failures.append(relation)
    result["status"] = "ok" if not failures else "failed"
    result["failures"] = failures
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit cached OHLCV cadence, ranges, and aggregation"
    )
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--symbol", default="BTC/USDT")
    args = parser.parse_args()
    result = audit(args.exchange, args.symbol)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
