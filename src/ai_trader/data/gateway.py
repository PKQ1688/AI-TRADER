"""行情获取网关抽象与 CCXT 实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol, Sequence

import ccxt


@dataclass(frozen=True)
class Candle:
    """标准化后的 K 线结构。"""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class DataGateway(Protocol):
    """行情访问接口，便于后续扩展其他数据源。"""

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> Sequence[Candle]:
        """返回指定交易对的 K 线序列。"""
        raise NotImplementedError  # pragma: no cover


@dataclass
class CcxtGateway:
    """使用 CCXT 拉取行情数据。"""

    exchange_id: str
    client_config: Dict[str, Any] = field(default_factory=dict)

    _client: ccxt.Exchange | None = field(init=False, default=None, repr=False)

    def _get_client(self) -> ccxt.Exchange:
        if self._client is not None:
            return self._client

        try:
            exchange_cls = getattr(ccxt, self.exchange_id)
        except AttributeError as exc:
            raise ValueError(f"不支持的交易所标识: {self.exchange_id}") from exc

        client = exchange_cls(self.client_config)
        client.load_markets()
        self._client = client
        return client

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> Sequence[Candle]:
        """从交易所拉取 K 线数据并转换为标准结构。"""

        client = self._get_client()
        raw_ohlcv: List[List[Any]] = client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

        candles: List[Candle] = []
        for entry in raw_ohlcv:
            if len(entry) < 6:
                continue
            timestamp, open_, high, low, close, volume = entry[:6]
            candles.append(
                Candle(
                    timestamp=int(timestamp),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(volume),
                )
            )

        if not candles:
            raise RuntimeError("未从交易所获取到有效的 K 线数据。")

        candles.sort(key=lambda c: c.timestamp)
        return candles
