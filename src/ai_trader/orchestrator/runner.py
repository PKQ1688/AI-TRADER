"""Agent Orchestrator：负责驱动交易 Agent 获得一次信号。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from agno.run.agent import RunOutput

from ..agents import create_trading_agent
from ..config import Settings, load_settings
from ..data import CcxtGateway


def _build_prompt(settings: Settings) -> str:
    return (
        "请基于最新行情生成一次交易建议，务必调用工具 `macd_signal` 获取指标，"
        f"分析交易对 {settings.symbol}，周期 {settings.timeframe}。"
    )


def _find_dict_with_macd(payload: Any) -> Optional[Dict[str, Any]]:
    """递归查找包含 MACD 结果的字典。"""

    if isinstance(payload, dict):
        if "macd" in payload and "signal" in payload:
            return payload
        for value in payload.values():
            result = _find_dict_with_macd(value)
            if result is not None:
                return result
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            result = _find_dict_with_macd(item)
            if result is not None:
                return result
    elif isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return _find_dict_with_macd(decoded)

    return None


def _extract_reasoning(payload: Dict[str, Any]) -> str:
    """从模型响应中提取可用的分析理由。"""

    parsed = payload.get("parsed")
    if isinstance(parsed, dict):
        reasoning = parsed.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") != "assistant":
                continue
            msg_content = message.get("content")
            if isinstance(msg_content, str) and msg_content.strip():
                return msg_content.strip()
            if isinstance(msg_content, list):
                for item in msg_content:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

    return "模型未返回有效理由。"


def _build_normalized_result(payload: Dict[str, Any], settings: Settings) -> Dict[str, Any]:
    """统一输出结构，确保关键字段齐备。"""

    parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}
    tool_result = _find_dict_with_macd(payload.get("tools")) or {}

    merged: Dict[str, Any] = {}
    if isinstance(tool_result, dict):
        merged.update(tool_result)
    if isinstance(parsed, dict):
        merged.update({k: v for k, v in parsed.items() if v is not None})

    reasoning = merged.get("reasoning")
    if not reasoning:
        merged["reasoning"] = _extract_reasoning(payload)

    merged.setdefault("signal", "hold")
    merged.setdefault("macd", tool_result.get("macd"))
    merged.setdefault("signal_line", tool_result.get("signal_line"))
    merged.setdefault("histogram", tool_result.get("histogram"))
    merged.setdefault("previous_histogram", tool_result.get("previous_histogram"))
    merged.setdefault("candles_used", tool_result.get("candles_used"))

    merged["symbol"] = merged.get("symbol") or settings.symbol
    merged["timeframe"] = merged.get("timeframe") or settings.timeframe

    return merged


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
    gateway = CcxtGateway(cfg.exchange_id)
    agent = create_trading_agent(cfg, gateway)

    response = agent.run(input=_build_prompt(cfg))
    if isinstance(response, RunOutput):
        return _serialize_response(response, cfg)

    # 理论上不会触发，兜底返回原对象
    return {"raw": response}
