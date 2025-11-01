"""MACD 指标工具实现。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from time import perf_counter
from typing import Any, Dict, Iterable, Optional

from agno.tools import Function

from ..data import DataGateway
from ..core.logging import get_logger

logger = get_logger(__name__)


class MacdSignal(str, Enum):
    """交易信号枚举。"""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class MacdToolOutput:
    """MACD 计算结果。"""

    signal: MacdSignal
    macd: float
    signal_line: float
    histogram: float
    previous_histogram: Optional[float]
    symbol: str
    timeframe: str
    candles_used: int


def _ema_series(values: Iterable[float], period: int) -> list[Optional[float]]:
    series = list(values)
    if len(series) < period:
        raise ValueError(f"序列长度不足以计算周期 {period} 的 EMA。")

    alpha = 2 / (period + 1)
    ema_values: list[Optional[float]] = []
    ema = 0.0

    for idx, price in enumerate(series):
        if idx == period - 1:
            window = series[:period]
            ema = sum(window) / period
            ema_values.append(ema)
        elif idx >= period:
            ema = price * alpha + ema * (1 - alpha)
            ema_values.append(ema)
        else:
            ema_values.append(None)

    return ema_values


def _calculate_macd(
    closes: Iterable[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float, Optional[float]]:
    closes_list = list(closes)
    if len(closes_list) < slow_period + signal_period:
        raise ValueError("收盘价数量不足，无法稳定计算 MACD。")

    fast_ema = _ema_series(closes_list, fast_period)
    slow_ema = _ema_series(closes_list, slow_period)

    macd_series: list[Optional[float]] = []
    for fast, slow in zip(fast_ema, slow_ema):
        if fast is None or slow is None:
            macd_series.append(None)
        else:
            macd_series.append(fast - slow)

    macd_values = [value for value in macd_series if value is not None]
    if len(macd_values) < signal_period:
        raise ValueError("MACD 序列长度不足，无法计算信号线。")

    signal_series = _ema_series(macd_values, signal_period)
    signal_values = [value for value in signal_series if value is not None]

    macd_line = macd_values[-1]
    signal_line = signal_values[-1]
    histogram = macd_line - signal_line

    previous_histogram: Optional[float] = None
    if len(signal_values) >= 2:
        previous_histogram = macd_values[-2] - signal_values[-2]

    return macd_line, signal_line, histogram, previous_histogram


def _determine_signal(
    histogram: float, previous_histogram: Optional[float]
) -> MacdSignal:
    if previous_histogram is None:
        return MacdSignal.HOLD

    if histogram > 0 and previous_histogram <= 0:
        return MacdSignal.BUY
    if histogram < 0 and previous_histogram >= 0:
        return MacdSignal.SELL
    return MacdSignal.HOLD


def build_macd_tool(
    data_gateway: DataGateway,
    *,
    default_symbol: str,
    default_timeframe: str,
    default_limit: int,
) -> Function:
    """构造可供 Agent 调用的 MACD 工具。"""

    def _entrypoint(
        *,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: Optional[int] = None,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> Dict[str, Any]:
        target_symbol = symbol or default_symbol
        target_timeframe = timeframe or default_timeframe
        target_limit = limit or default_limit

        fetch_start = perf_counter()
        candles = data_gateway.fetch_ohlcv(
            target_symbol, target_timeframe, target_limit
        )
        fetch_duration = perf_counter() - fetch_start
        logger.info(
            "MACD 工具获取烛线耗时 %.3fs (symbol=%s, timeframe=%s, limit=%s)",
            fetch_duration,
            target_symbol,
            target_timeframe,
            target_limit,
        )
        closes = [candle.close for candle in candles]

        macd_start = perf_counter()
        macd_line, signal_line, histogram, prev_hist = _calculate_macd(
            closes,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        )
        macd_duration = perf_counter() - macd_start
        logger.info(
            "MACD 指标计算耗时 %.3fs (candles=%s, fast=%s, slow=%s, signal=%s)",
            macd_duration,
            len(closes),
            fast_period,
            slow_period,
            signal_period,
        )
        signal = _determine_signal(histogram, prev_hist)

        output = MacdToolOutput(
            signal=signal,
            macd=macd_line,
            signal_line=signal_line,
            histogram=histogram,
            previous_histogram=prev_hist,
            symbol=target_symbol,
            timeframe=target_timeframe,
            candles_used=len(closes),
        )

        result = asdict(output)
        result["signal"] = output.signal.value
        return result

    return Function(
        name="macd_signal",
        description="根据最新行情数据计算 MACD，并给出买卖信号。",
        parameters={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "交易对，默认读取配置。",
                },
                "timeframe": {
                    "type": "string",
                    "description": "K 线周期，默认读取配置。",
                },
                "limit": {
                    "type": "integer",
                    "description": "拉取的 K 线数量，默认读取配置。",
                    "minimum": 50,
                },
                "fast_period": {
                    "type": "integer",
                    "description": "MACD 快速 EMA 周期，默认 12。",
                    "minimum": 2,
                },
                "slow_period": {
                    "type": "integer",
                    "description": "MACD 慢速 EMA 周期，默认 26。",
                    "minimum": 2,
                },
                "signal_period": {
                    "type": "integer",
                    "description": "信号线 EMA 周期，默认 9。",
                    "minimum": 2,
                },
            },
        },
        entrypoint=_entrypoint,
    )
