from __future__ import annotations

import csv
import os
import time
import warnings
from pathlib import Path
from typing import Iterable

from ai_trader.types import Bar, parse_utc_time


def _timeframe_to_ms(timeframe: str) -> int:
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    if unit == "m":
        return value * 60 * 1000
    if unit == "h":
        return value * 60 * 60 * 1000
    if unit == "d":
        return value * 24 * 60 * 60 * 1000
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _data_root() -> Path:
    return Path(os.getenv("AI_TRADER_DATA_DIR", "data/raw"))


def _cache_path(exchange: str, symbol: str, timeframe: str) -> Path:
    symbol_key = symbol.replace("/", "")
    return _data_root() / exchange / symbol_key / f"{timeframe}.csv"


def cache_path_for(exchange: str, symbol: str, timeframe: str) -> Path:
    return _cache_path(exchange, symbol, timeframe)


def _read_csv(path: Path) -> list[Bar]:
    if not path.exists():
        return []
    bars: list[Bar] = []
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(
                Bar(
                    time=row["time"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0) or 0.0),
                )
            )
    bars.sort(key=lambda x: x.time)
    return bars


def _write_csv(path: Path, bars: Iterable[Bar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for bar in bars:
            writer.writerow(bar.to_dict())


def _to_ms(dt) -> int:
    return int(parse_utc_time(dt).timestamp() * 1000)


def _from_ms(ms: int) -> str:
    return parse_utc_time(ms / 1000).isoformat().replace("+00:00", "Z")


def _merge_bars(left: list[Bar], right: list[Bar]) -> list[Bar]:
    merged: dict[int, Bar] = {int(item.time.timestamp()): item for item in left}
    for item in right:
        merged[int(item.time.timestamp())] = item
    return [merged[k] for k in sorted(merged.keys())]


def _bars_from_ohlcv_rows(rows: Iterable[Iterable[float]], start_ms: int, end_ms: int) -> list[Bar]:
    bars: list[Bar] = []
    for item in rows:
        ts = int(item[0])
        if ts < start_ms or ts > end_ms:
            continue
        bars.append(
            Bar(
                time=ts / 1000,
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[5] or 0.0),
            )
        )
    return _merge_bars([], bars)


def _find_missing_ranges(cached: list[Bar], start_utc: str, end_utc: str, timeframe: str) -> list[tuple[int, int]]:
    step = _timeframe_to_ms(timeframe)
    start_ms = _to_ms(start_utc)
    end_ms = _to_ms(end_utc)
    if end_ms < start_ms:
        return []

    present = {
        int(item.time.timestamp() * 1000)
        for item in cached
        if start_ms <= int(item.time.timestamp() * 1000) <= end_ms
    }

    missing: list[tuple[int, int]] = []
    cursor_start: int | None = None

    ts = start_ms
    while ts <= end_ms:
        if ts not in present:
            if cursor_start is None:
                cursor_start = ts
        else:
            if cursor_start is not None:
                missing.append((cursor_start, ts - step))
                cursor_start = None
        ts += step

    if cursor_start is not None:
        missing.append((cursor_start, end_ms))

    return missing


def _count_missing_bars(missing: list[tuple[int, int]], timeframe: str) -> int:
    step = _timeframe_to_ms(timeframe)
    return sum(((end_ms - start_ms) // step) + 1 for start_ms, end_ms in missing)


def _fetch_with_ccxt_ms(exchange: str, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
    try:
        import ccxt
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("ccxt is required to fetch online data") from exc

    if not hasattr(ccxt, exchange):
        raise ValueError(f"Unsupported exchange: {exchange}")

    client = getattr(ccxt, exchange)({"enableRateLimit": True, "timeout": 30000})
    step = _timeframe_to_ms(timeframe)

    rows: list[list[float]] = []
    cursor = start_ms
    while cursor <= end_ms:
        batch = client.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + step
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    return _bars_from_ohlcv_rows(rows, start_ms=start_ms, end_ms=end_ms)


def _fetch_with_binance_rest_ms(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
    try:
        import requests
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("requests is required for Binance REST fallback") from exc

    if "/" not in symbol:
        raise ValueError("symbol must be in BASE/QUOTE format, e.g. BTC/USDT")
    base, quote = symbol.split("/", 1)
    pair = f"{base}{quote}"

    rows: list[list[float]] = []
    cursor = start_ms
    step = _timeframe_to_ms(timeframe)

    while cursor <= end_ms:
        response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": pair,
                "interval": timeframe,
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
        next_cursor = int(batch[-1][0]) + step
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    return _bars_from_ohlcv_rows(rows, start_ms=start_ms, end_ms=end_ms)


def _fetch_range_with_retry(exchange: str, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Bar]:
    max_retries = 4
    last_error: Exception | None = None

    for idx in range(max_retries):
        try:
            if exchange.lower() == "binance":
                return _fetch_with_binance_rest_ms(symbol=symbol, timeframe=timeframe, start_ms=start_ms, end_ms=end_ms)
            return _fetch_with_ccxt_ms(exchange=exchange, symbol=symbol, timeframe=timeframe, start_ms=start_ms, end_ms=end_ms)
        except Exception as exc:
            last_error = exc
            time.sleep(min(8, 2**idx))

    if exchange.lower() == "binance":
        # fallback once with ccxt (some network path差异时可命中)
        try:
            return _fetch_with_ccxt_ms(exchange=exchange, symbol=symbol, timeframe=timeframe, start_ms=start_ms, end_ms=end_ms)
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return []


def load_ohlcv(exchange: str, symbol: str, timeframe: str, start_utc: str, end_utc: str) -> list[Bar]:
    """Load OHLCV with cache-first strategy and incremental missing-range refill."""
    start = parse_utc_time(start_utc)
    end = parse_utc_time(end_utc)
    if end < start:
        raise ValueError("end_utc must be >= start_utc")

    path = _cache_path(exchange=exchange, symbol=symbol, timeframe=timeframe)
    cached = _read_csv(path)

    missing = _find_missing_ranges(cached, start_utc=start_utc, end_utc=end_utc, timeframe=timeframe)
    allowed = int(os.getenv("AI_TRADER_MAX_MISSING_BARS", "3"))
    missing_bars = _count_missing_bars(missing, timeframe=timeframe) if missing else 0

    if missing and cached and missing_bars <= allowed:
        summary = ", ".join(f"[{_from_ms(a)} ~ {_from_ms(b)}]" for a, b in missing[:5])
        warnings.warn(
            f"Cache has minor gaps for {exchange} {symbol} {timeframe}: "
            f"missing_bars={missing_bars}, missing={summary}",
            stacklevel=2,
        )
        return [item for item in cached if start <= item.time <= end]

    if missing:
        merged = cached[:]
        for miss_start_ms, miss_end_ms in missing:
            fetched = _fetch_range_with_retry(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                start_ms=miss_start_ms,
                end_ms=miss_end_ms,
            )
            if fetched:
                merged = _merge_bars(merged, fetched)

        merged.sort(key=lambda x: x.time)
        _write_csv(path, merged)
        cached = merged

        remaining = _find_missing_ranges(cached, start_utc=start_utc, end_utc=end_utc, timeframe=timeframe)
        if remaining:
            missing_bars = _count_missing_bars(remaining, timeframe=timeframe)
            summary = ", ".join(f"[{_from_ms(a)} ~ {_from_ms(b)}]" for a, b in remaining[:5])
            if missing_bars > allowed:
                raise RuntimeError(
                    f"Cache still incomplete for {exchange} {symbol} {timeframe}. "
                    f"missing_bars={missing_bars}, allowed={allowed}, missing={summary}"
                )
            warnings.warn(
                f"Cache has minor gaps for {exchange} {symbol} {timeframe}: "
                f"missing_bars={missing_bars}, missing={summary}",
                stacklevel=2,
            )

    return [item for item in cached if start <= item.time <= end]
