"""Agent Orchestrator：负责驱动交易 Agent 获得一次信号。"""

from __future__ import annotations

import ast
import json
from time import perf_counter
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


def _stringify_for_log(data: Any) -> str:
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False)
    except TypeError:
        return str(data)


def _log_model_io(payload: Dict[str, Any]) -> None:
    from ..core.logging.config import VERBOSE_LOGGING, DEBUG_LOGGING

    # 调试模式：显示完整的LLM交互信息，但格式化更易读
    if DEBUG_LOGGING:
        input_payload = payload.get("input")
        if isinstance(input_payload, dict):
            logger.info("📝 AI请求 -> %s", _stringify_for_log(input_payload.get("input_content", input_payload)))

        messages = payload.get("messages")
        if messages:
            # 解析messages，显示关键信息
            for i, msg in enumerate(messages):
                if isinstance(msg, dict):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    tool_name = msg.get("tool_name", "")

                    if role == "system":
                        logger.info("🔧 系统指令 -> 分析策略设定")
                    elif role == "user":
                        logger.info("👤 用户请求 -> %s", content[:80] + "..." if len(content) > 80 else content)
                    elif role == "tool":
                        # 显示工具结果的关键数据
                        logger.info("🔨 工具结果 -> %s", tool_name or "macd_signal")
                        if tool_name == "macd_signal" and content:
                            try:
                                result_data = ast.literal_eval(content) if isinstance(content, str) else content
                                if isinstance(result_data, dict):
                                    macd = result_data.get("macd", 0)
                                    signal_line = result_data.get("signal_line", 0)
                                    histogram = result_data.get("histogram", 0)
                                    logger.info("   📈 MACD: %.2f, Signal: %.2f, Histogram: %.2f", macd, signal_line, histogram)
                            except:
                                logger.info("   📈 数据: %s", str(content)[:100] + "..." if len(str(content)) > 100 else str(content))
                    elif role == "assistant":
                        # 检查是否有工具调用
                        tool_calls = msg.get("tool_calls", [])
                        if tool_calls:
                            # 显示工具调用参数
                            for call in tool_calls:
                                func = call.get("function", {})
                                args = func.get("arguments", "{}")
                                logger.info("🤖 AI决策 -> 调用工具 %s(%s)", func.get("name", "未知"), args)
                        else:
                            # 解析AI的最终回复
                            try:
                                result = json.loads(content) if isinstance(content, str) else content
                                signal = result.get("signal", "未知") if isinstance(result, dict) else "解析失败"
                                logger.info("🤖 AI决策 -> %s", signal)
                            except:
                                logger.info("🤖 AI决策 -> %s", content[:50] + "..." if len(content) > 50 else content)

                        # 显示token使用情况
                        metrics = msg.get("metrics", {})
                        if metrics:
                            input_tokens = metrics.get("input_tokens", 0)
                            output_tokens = metrics.get("output_tokens", 0)
                            total_tokens = metrics.get("total_tokens", 0)
                            logger.info("   💰 Token使用: 输入%d + 输出%d = 总计%d", input_tokens, output_tokens, total_tokens)

        logger.info("📊 原始输出 -> %s", _stringify_for_log(payload.get("content")))
        logger.info("✅ 解析结果 -> %s", _stringify_for_log(payload.get("parsed")))
    elif VERBOSE_LOGGING:
        # 详细模式：显示工具调用和AI分析结果
        tools = payload.get("tools")
        if tools and isinstance(tools, list) and len(tools) > 0:
            tool = tools[0]  # 通常只有一个工具调用
            tool_name = tool.get("tool_name", "未知工具")

            # 解析工具结果
            result_str = tool.get("result", "{}")
            try:
                # 使用ast.literal_eval安全解析Python字典字符串
                if isinstance(result_str, str):
                    result_data = ast.literal_eval(result_str)
                else:
                    result_data = result_str
                if isinstance(result_data, dict):
                    macd = result_data.get("macd", 0)
                    signal_line = result_data.get("signal_line", 0)
                    histogram = result_data.get("histogram", 0)
                    candles_used = result_data.get("candles_used", 0)

                    logger.info("MACD指标 -> MACD: %.2f, Signal: %.2f, Histogram: %.2f (K线数: %d)",
                              macd, signal_line, histogram, candles_used)
                else:
                    logger.info("工具调用 -> %s", tool_name)
            except (ValueError, TypeError, AttributeError, SyntaxError):
                logger.info("工具调用 -> %s", tool_name)

        parsed = payload.get("parsed")
        if isinstance(parsed, dict) and "signal" in parsed:
            signal = parsed.get("signal")
            logger.info("AI分析结果: %s", signal)
    else:
        # 简洁模式：只显示最终结果
        parsed = payload.get("parsed")
        if isinstance(parsed, dict) and "signal" in parsed:
            signal = parsed.get("signal")
            logger.info("AI分析结果: %s", signal)


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
    logger.info("开始分析 %s (%s)", cfg.symbol, cfg.timeframe)

    gateway_start = perf_counter()
    gateway = CcxtGateway(cfg.exchange_id)
    gateway_duration = perf_counter() - gateway_start
    logger.info(
        "CcxtGateway 初始化耗时 %.3fs (exchange=%s)",
        gateway_duration,
        cfg.exchange_id,
    )

    agent_start = perf_counter()
    agent = create_trading_agent(cfg, gateway)
    agent_duration = perf_counter() - agent_start
    logger.info("TradingAgent 构建耗时 %.3fs", agent_duration)

    run_start = perf_counter()
    response = agent.run(input=prompt)
    run_duration = perf_counter() - run_start
    logger.info("Agent.run 总耗时 %.3fs", run_duration)
    if isinstance(response, RunOutput):
        serialized = _serialize_response(response, cfg)
        _log_model_io(serialized)
        logger.info("Normalized signal -> %s", serialized.get("normalized"))
        return serialized

    logger.info("Agent returned non-standard payload -> %s", response)
    return {"raw": response}
