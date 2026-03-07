from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
import mimetypes
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from _script_utils import ensure_src_on_path

ensure_src_on_path()

from ai_trader.chan.config import get_chan_config
from ai_trader.chan.review import build_review_session_payload, build_review_snapshot
from ai_trader.data.binance_ohlcv import load_ohlcv
from ai_trader.indicators import compute_macd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start a local Chan review page for manual structure inspection"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe-main", default="4h")
    parser.add_argument("--timeframe-sub", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00Z")
    parser.add_argument("--end", default="2025-12-31T23:59:59Z")
    parser.add_argument(
        "--chan-mode",
        default="strict_kline8",
        choices=("strict_kline8", "pragmatic"),
    )
    parser.add_argument("--window-main", type=int, default=120)
    parser.add_argument("--window-sub", type=int, default=180)
    return parser.parse_args()


@dataclass(slots=True)
class LoadedSession:
    params: dict[str, Any]
    bars_main: list[Any]
    bars_sub: list[Any]
    macd_main: list[Any]
    macd_sub: list[Any]
    payload: dict[str, Any]


class ReviewAppState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.asset_root = Path(__file__).resolve().parents[1] / "web" / "chan_review"
        self.defaults = {
            "exchange": args.exchange,
            "symbol": args.symbol,
            "timeframe_main": args.timeframe_main,
            "timeframe_sub": args.timeframe_sub,
            "start": args.start,
            "end": args.end,
            "chan_mode": args.chan_mode,
            "window_main": args.window_main,
            "window_sub": args.window_sub,
            "host": args.host,
            "port": args.port,
        }

    def resolve_params(self, query: dict[str, list[str]]) -> dict[str, Any]:
        def pick(name: str, default: Any) -> Any:
            values = query.get(name)
            if not values or values[0] == "":
                return default
            return values[0]

        return {
            "exchange": pick("exchange", self.defaults["exchange"]),
            "symbol": pick("symbol", self.defaults["symbol"]),
            "timeframe_main": pick("timeframe_main", self.defaults["timeframe_main"]),
            "timeframe_sub": pick("timeframe_sub", self.defaults["timeframe_sub"]),
            "start": pick("start", self.defaults["start"]),
            "end": pick("end", self.defaults["end"]),
            "chan_mode": pick("chan_mode", self.defaults["chan_mode"]),
            "window_main": int(pick("window_main", self.defaults["window_main"])),
            "window_sub": int(pick("window_sub", self.defaults["window_sub"])),
        }

    @lru_cache(maxsize=8)
    def load_session(
        self,
        exchange: str,
        symbol: str,
        timeframe_main: str,
        timeframe_sub: str,
        start: str,
        end: str,
        chan_mode: str,
    ) -> LoadedSession:
        bars_main = load_ohlcv(exchange, symbol, timeframe_main, start, end)
        bars_sub = load_ohlcv(exchange, symbol, timeframe_sub, start, end)
        bars_main.sort(key=lambda item: item.time)
        bars_sub.sort(key=lambda item: item.time)

        payload = build_review_session_payload(
            bars_main=bars_main,
            bars_sub=bars_sub,
            exchange=exchange,
            symbol=symbol,
            timeframe_main=timeframe_main,
            timeframe_sub=timeframe_sub,
            start=start,
            end=end,
        )

        return LoadedSession(
            params={
                "exchange": exchange,
                "symbol": symbol,
                "timeframe_main": timeframe_main,
                "timeframe_sub": timeframe_sub,
                "start": start,
                "end": end,
                "chan_mode": chan_mode,
            },
            bars_main=bars_main,
            bars_sub=bars_sub,
            macd_main=compute_macd(bars_main),
            macd_sub=compute_macd(bars_sub),
            payload=payload,
        )

    def defaults_payload(self) -> dict[str, Any]:
        return {"defaults": self.defaults}

    def session_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self.load_session(
            params["exchange"],
            params["symbol"],
            params["timeframe_main"],
            params["timeframe_sub"],
            params["start"],
            params["end"],
            params["chan_mode"],
        )
        return {
            "params": session.params,
            "session": session.payload,
        }

    def snapshot_payload(self, params: dict[str, Any], asof: str | None) -> dict[str, Any]:
        session = self.load_session(
            params["exchange"],
            params["symbol"],
            params["timeframe_main"],
            params["timeframe_sub"],
            params["start"],
            params["end"],
            params["chan_mode"],
        )
        if not session.payload["main_times"]:
            raise ValueError("主级别 K 线为空，无法生成回放快照")

        resolved_asof = asof or session.payload["main_times"][-1]
        chan_config = get_chan_config(params["chan_mode"])
        snapshot = build_review_snapshot(
            bars_main=session.bars_main,
            bars_sub=session.bars_sub,
            macd_main=session.macd_main,
            macd_sub=session.macd_sub,
            asof_time=resolved_asof,
            exchange=params["exchange"],
            symbol=params["symbol"],
            timeframe_main=params["timeframe_main"],
            timeframe_sub=params["timeframe_sub"],
            start=params["start"],
            end=params["end"],
            window_main=params["window_main"],
            window_sub=params["window_sub"],
            chan_config=chan_config,
        )
        return {
            "params": session.params,
            "session": session.payload,
            "snapshot": snapshot,
        }


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "ChanReviewHTTP/0.1"

    @property
    def app_state(self) -> ReviewAppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self._serve_asset("index.html")
            return
        if path == "/app.js":
            self._serve_asset("app.js")
            return
        if path == "/styles.css":
            self._serve_asset("styles.css")
            return
        if path == "/api/defaults":
            self._handle_json(self.app_state.defaults_payload())
            return
        if path == "/api/session":
            self._handle_api(query, mode="session")
            return
        if path == "/api/snapshot":
            self._handle_api(query, mode="snapshot")
            return
        if path == "/healthz":
            self._handle_json({"ok": True})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _handle_api(self, query: dict[str, list[str]], mode: str) -> None:
        try:
            params = self.app_state.resolve_params(query)
            if mode == "session":
                payload = self.app_state.session_payload(params)
            else:
                asof = query.get("asof", [None])[0]
                payload = self.app_state.snapshot_payload(params, asof=asof)
            self._handle_json(payload)
        except Exception as exc:
            self._handle_json(
                {
                    "error": {
                        "message": str(exc),
                        "hint": (
                            "若本地缓存不足，请先运行 scripts/warm_cache.py 预热对应交易对和周期数据。"
                        ),
                    }
                },
                status=HTTPStatus.BAD_REQUEST,
            )

    def _handle_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_asset(self, relative_path: str) -> None:
        asset_path = (self.app_state.asset_root / relative_path).resolve()
        root = self.app_state.asset_root.resolve()
        if root not in asset_path.parents and asset_path != root:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not asset_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        mime_type, _ = mimetypes.guess_type(str(asset_path))
        body = asset_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type", f"{mime_type or 'application/octet-stream'}; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ReviewHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], app_state: ReviewAppState) -> None:
        super().__init__(server_address, ReviewRequestHandler)
        self.app_state = app_state


def main() -> None:
    args = parse_args()
    app_state = ReviewAppState(args)

    try:
        app_state.session_payload(app_state.resolve_params({}))
    except Exception as exc:
        print(f"[warn] default session preload failed: {exc}")

    server = ReviewHTTPServer((args.host, args.port), app_state)
    print(
        f"Chan review page running at http://{args.host}:{args.port} "
        f"(symbol={args.symbol}, timeframe={args.timeframe_main}/{args.timeframe_sub})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
