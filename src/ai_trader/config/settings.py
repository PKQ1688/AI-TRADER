"""统一加载与维护运行时配置。"""

from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

DEFAULT_EXCHANGE_ID = "binance"
DEFAULT_SYMBOL = "BTC/USDT"
DEFAULT_TIMEFRAME = "4h"
DEFAULT_CANDLE_LIMIT = 200


@dataclass(frozen=True)
class Settings:
    """运行时配置快照。"""

    exchange_id: str = DEFAULT_EXCHANGE_ID
    symbol: str = DEFAULT_SYMBOL
    timeframe: str = DEFAULT_TIMEFRAME
    candle_limit: int = DEFAULT_CANDLE_LIMIT
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: Optional[str] = None


def load_settings(
    *,
    exchange_id: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    candle_limit: Optional[int] = None,
) -> Settings:
    """生成配置实例，非敏感配置来自默认值或函数参数。"""

    api_key = getenv("OPENAI_API_KEY")
    base_url = getenv("OPENAI_BASE_URL")
    model = getenv("AI_TRADER_MODEL")

    return Settings(
        exchange_id=exchange_id or DEFAULT_EXCHANGE_ID,
        symbol=symbol or DEFAULT_SYMBOL,
        timeframe=timeframe or DEFAULT_TIMEFRAME,
        candle_limit=candle_limit or DEFAULT_CANDLE_LIMIT,
        openai_api_key=api_key,
        openai_base_url=base_url,
        openai_model=model,
    )
