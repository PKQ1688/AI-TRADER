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
        "始终调用工具 `macd_signal` 获取最新指标；禁止凭空编造数据。",
        "拿到工具返回后，评估 MACD 与柱状图的变化，给出简洁的买/卖/观望建议。",
        "最终仅输出一个 JSON 对象，字段包含 signal、reasoning、macd、signal_line、histogram、previous_histogram、symbol、timeframe、candles_used。",
        "若工具计算失败，应返回 reason 字段描述原因，并将 signal 设置为 hold。",
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
        use_json_mode=False,
    )
