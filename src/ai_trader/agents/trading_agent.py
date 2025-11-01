"""构造交易 Agent。"""

from __future__ import annotations

from typing import Any, MutableSequence, Optional, List, Sequence

from agno.agent import Agent
from agno.models.deepseek.deepseek import DeepSeek

from ..config import Settings
from ..data import DataGateway
from ..indicators import build_macd_tool


def _build_instructions(settings: Settings) -> List[str]:
    symbol = settings.symbol
    timeframe = settings.timeframe
    return [
        f"你是面向专业交易员的量化助手，负责分析交易对 {symbol} 在周期 {timeframe} 的行情。",
        "必须调用工具 `macd_signal` 获取最新指标，严禁凭空编造行情数据。",
        "基于工具返回的 MACD 与柱状图变化，判断应买入、卖出或继续观望。",
        '最终仅输出 JSON 对象 {"signal": "buy"|"sell"|"hold"}，不得包含任何额外字段或说明文字。',
    ]


def _build_model(settings: Settings) -> DeepSeek:
    model_params: dict[str, Any] = {"id": settings.openai_model}

    if settings.openai_api_key:
        model_params["api_key"] = settings.openai_api_key
    if settings.openai_base_url:
        model_params["base_url"] = settings.openai_base_url

    return DeepSeek(**model_params)


def create_trading_agent(
    settings: Settings,
    data_gateway: DataGateway,
    tools: Optional[Sequence[Any]] = None,
) -> Agent:
    """创建最小可用的交易 Agent。"""

    toolset: MutableSequence[Any] = list(tools) if tools else []
    toolset.append(
        build_macd_tool(
            data_gateway,
            default_symbol=settings.symbol,
            default_timeframe=settings.timeframe,
            default_limit=settings.candle_limit,
        )
    )

    model = _build_model(settings)
    instructions = _build_instructions(settings)

    return Agent(
        name="TradingAgent",
        model=model,
        tools=toolset,
        tool_choice="auto",
        instructions=instructions,
        dependencies={"settings": settings},
        add_dependencies_to_context=False,
    )
