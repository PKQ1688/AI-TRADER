from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_trader.types import FundingRate, parse_utc_time

_MAX_FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000
_TIMESTAMP_JITTER_MS = 1000


def _data_root() -> Path:
    return Path(os.getenv("AI_TRADER_DATA_DIR", "data/raw"))


def funding_cache_path_for(exchange: str, symbol: str) -> Path:
    symbol_key = symbol.replace("/", "").split(":", 1)[0]
    return _data_root() / exchange / symbol_key / "funding.csv"


def _coverage_path(exchange: str, symbol: str) -> Path:
    return funding_cache_path_for(exchange, symbol).with_name(
        "funding_coverage.csv"
    )


def _to_ms(value) -> int:
    return int(parse_utc_time(value).timestamp() * 1000)


def _read_csv(path: Path) -> list[FundingRate]:
    if not path.exists():
        return []
    out: list[FundingRate] = []
    with path.open("r", newline="") as f:
        for row in csv.DictReader(f):
            out.append(
                FundingRate(
                    time=row["time"],
                    rate=float(row["rate"]),
                    mark_price=float(row.get("mark_price", 0.0) or 0.0),
                )
            )
    out.sort(key=lambda item: item.time)
    return out


def _write_csv(path: Path, rates: list[FundingRate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["time", "rate", "mark_price"],
        )
        writer.writeheader()
        for item in rates:
            writer.writerow(item.to_dict())


def _merge_rates(
    left: list[FundingRate], right: list[FundingRate]
) -> list[FundingRate]:
    merged = {int(item.time.timestamp() * 1000): item for item in left}
    for item in right:
        merged[int(item.time.timestamp() * 1000)] = item
    return [merged[key] for key in sorted(merged)]


def _read_coverage(path: Path) -> list[tuple[int, int]]:
    if not path.exists():
        return []
    out = []
    with path.open("r", newline="") as f:
        for row in csv.DictReader(f):
            out.append((int(row["start_ms"]), int(row["end_ms"])))
    return _merge_coverage(out)


def _write_coverage(path: Path, coverage: list[tuple[int, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["start_ms", "end_ms"])
        writer.writeheader()
        for start_ms, end_ms in _merge_coverage(coverage):
            writer.writerow({"start_ms": start_ms, "end_ms": end_ms})


def _merge_coverage(
    coverage: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start_ms, end_ms in sorted(coverage):
        if not merged or start_ms > merged[-1][1] + 1:
            merged.append((start_ms, end_ms))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms))
    return merged


def _uncovered_ranges(
    coverage: list[tuple[int, int]], start_ms: int, end_ms: int
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cursor = start_ms
    for covered_start, covered_end in _merge_coverage(coverage):
        if covered_end < cursor:
            continue
        if covered_start > end_ms:
            break
        if covered_start > cursor:
            out.append((cursor, min(end_ms, covered_start - 1)))
        cursor = max(cursor, covered_end + 1)
        if cursor > end_ms:
            break
    if cursor <= end_ms:
        out.append((cursor, end_ms))
    return out


def _validate_coverage_density(
    rates: list[FundingRate], start_ms: int, end_ms: int
) -> None:
    if end_ms - start_ms < _MAX_FUNDING_INTERVAL_MS:
        return
    timestamps = [
        int(item.time.timestamp() * 1000)
        for item in rates
        if start_ms <= int(item.time.timestamp() * 1000) <= end_ms
    ]
    if not timestamps:
        raise RuntimeError("Binance returned no funding records for an 8h+ range")

    max_gap = _MAX_FUNDING_INTERVAL_MS + _TIMESTAMP_JITTER_MS
    gaps = [
        timestamps[0] - start_ms,
        *(current - previous for previous, current in zip(timestamps, timestamps[1:])),
        end_ms - timestamps[-1],
    ]
    if max(gaps) > max_gap:
        raise RuntimeError(
            "Binance funding history contains an interval longer than 8h"
        )


def _pair_symbol(symbol: str) -> str:
    contract = symbol.split(":", 1)[0]
    if "/" not in contract:
        raise ValueError("symbol must be in BASE/QUOTE format, e.g. BTC/USDT")
    base, quote = contract.split("/", 1)
    return f"{base}{quote}"


def _fetch_funding_range(
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[FundingRate]:
    try:
        import requests
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("requests is required to fetch Binance funding rates") from exc

    rows: list[dict] = []
    cursor = start_ms
    while cursor <= end_ms:
        response = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={
                "symbol": _pair_symbol(symbol),
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1]["fundingTime"]) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    out = []
    for row in rows:
        timestamp = int(row["fundingTime"])
        if timestamp < start_ms or timestamp > end_ms:
            continue
        out.append(
            FundingRate(
                time=datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc),
                rate=float(row["fundingRate"]),
                mark_price=float(row.get("markPrice", 0.0) or 0.0),
            )
        )
    return _merge_rates([], out)


def _fetch_with_retry(
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[FundingRate]:
    last_error: Exception | None = None
    for idx in range(4):
        try:
            return _fetch_funding_range(symbol, start_ms, end_ms)
        except Exception as exc:
            last_error = exc
            time.sleep(min(8, 2**idx))
    if last_error is not None:
        raise last_error
    return []


def load_funding_rates(
    exchange: str,
    symbol: str,
    start_utc: str,
    end_utc: str,
) -> list[FundingRate]:
    """Load complete Binance USDT-perpetual funding history.

    Fetched coverage is recorded separately from the observations, so the
    loader does not invent a fixed settlement grid.  Binance may shorten the
    standard eight-hour interval; an observed gap longer than eight hours is
    still treated as a hard data-quality failure.
    """
    if exchange.lower() != "binanceusdm":
        raise ValueError("funding rates are only supported for binanceusdm")

    start_ms = _to_ms(start_utc)
    end_ms = _to_ms(end_utc)
    if end_ms < start_ms:
        raise ValueError("end_utc must be >= start_utc")

    path = funding_cache_path_for(exchange, symbol)
    coverage_path = _coverage_path(exchange, symbol)
    rates = _read_csv(path)
    coverage = _read_coverage(coverage_path)
    for missing_start, missing_end in _uncovered_ranges(
        coverage, start_ms, end_ms
    ):
        fetched = _fetch_with_retry(symbol, missing_start, missing_end)
        _validate_coverage_density(fetched, missing_start, missing_end)
        rates = _merge_rates(
            rates,
            fetched,
        )
        coverage.append((missing_start, missing_end))
    _write_csv(path, rates)
    _write_coverage(coverage_path, coverage)
    _validate_coverage_density(rates, start_ms, end_ms)

    start = parse_utc_time(start_utc)
    end = parse_utc_time(end_utc)
    return [item for item in rates if start <= item.time <= end]
