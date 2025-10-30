"""Agent Orchestrator：负责驱动交易 Agent 获得一次信号。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from agno.run.agent import RunOutput

from ..agents import create_trading_agent
from ..config import Settings, load_settings
from ..data import CcxtGateway
from ..core.logging import get_logger

logger = get_logger(__name__)


def _build_prompt(settings: Settings) -> str:
    return (
        "请基于最新行情生成一次交易建议，务必调用工具 `macd_signal` 获取指标，"
        f"分析交易对 {settings.symbol}，周期 {settings.timeframe}。"
    )


def _find_signal_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """递归寻找包含信号字段的结构，优先复用工具输出。"""

    if isinstance(payload, dict):
        if "signal" in payload:
            return payload
        for value in payload.values():
            result = _find_signal_payload(value)
            if result is not None:
                return result
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            result = _find_signal_payload(item)
            if result is not None:
                return result
    elif isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return _find_signal_payload(decoded)

    return None


def _build_normalized_result(
    payload: Dict[str, Any], settings: Settings
) -> Dict[str, Any]:
    """提取模型或工具返回的信号，统一封装。"""

    parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}
    signal = parsed.get("signal") if isinstance(parsed, dict) else None

    if not signal:
        tool_result = _find_signal_payload(payload.get("tools"))
        if isinstance(tool_result, dict):
            tool_signal = tool_result.get("signal")
            if isinstance(tool_signal, str):
                signal = tool_signal

    if not signal:
        signal = "hold"

    return {
        "signal": signal,
        "symbol": settings.symbol,
        "timeframe": settings.timeframe,
    }


def _serialize_response(response: RunOutput, settings: Settings) -> Dict[str, Any]:
    payload = response.to_dict()
    content = payload.get("content")
    if isinstance(content, str):
        try:
            payload["parsed"] = json.loads(content)
        except json.JSONDecodeError:
            payload["parsed"] = None
    payload["normalized"] = _build_normalized_result(payload, settings)
    return payload


def run_once(config: Optional[Settings] = None) -> Dict[str, Any]:
    """执行一次端到端信号生成流程。"""

    cfg = config or load_settings()
    prompt = _build_prompt(cfg)
    logger.info("LLM prompt -> %s", prompt)

    gateway = CcxtGateway(cfg.exchange_id)
    agent = create_trading_agent(cfg, gateway)

    response = agent.run(input=prompt)
    if isinstance(response, RunOutput):
        serialized = _serialize_response(response, cfg)
        logger.info("LLM raw content -> %s", serialized.get("content"))
        logger.info("LLM parsed output -> %s", serialized.get("parsed"))
        logger.info("Normalized signal -> %s", serialized.get("normalized"))
        return serialized

    logger.info("Agent returned non-standard payload -> %s", response)
    return {"raw": response}
