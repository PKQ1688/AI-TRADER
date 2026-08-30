from __future__ import annotations
# ruff: noqa: E402

import argparse

from _script_utils import ensure_src_on_path

ensure_src_on_path()

from ai_trader.data import (
    cache_path_for,
    funding_cache_path_for,
    load_funding_rates,
    load_ohlcv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm local OHLCV cache for backtests")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--start", default="2022-02-10T00:00:00Z")
    parser.add_argument("--end", default="2026-02-10T00:00:00Z")
    parser.add_argument("--timeframes", nargs="+", default=["4h", "1h"])
    parser.add_argument(
        "--funding",
        action="store_true",
        help="also warm Binance USDT-perpetual funding history",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print(f"Warming cache: {args.exchange} {args.symbol} {args.start} -> {args.end}")
    for tf in args.timeframes:
        bars = load_ohlcv(
            exchange=args.exchange,
            symbol=args.symbol,
            timeframe=tf,
            start_utc=args.start,
            end_utc=args.end,
        )
        path = cache_path_for(args.exchange, args.symbol, tf)
        print(f"[{tf}] bars={len(bars)} cache={path}")

    if args.funding:
        rates = load_funding_rates(
            exchange=args.exchange,
            symbol=args.symbol,
            start_utc=args.start,
            end_utc=args.end,
        )
        path = funding_cache_path_for(args.exchange, args.symbol)
        print(f"[funding] rates={len(rates)} cache={path}")


if __name__ == "__main__":
    main()
