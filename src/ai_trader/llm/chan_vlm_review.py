from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from ai_trader.llm.openai_compat import build_chat_completion_payload


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_diagnostic_context(diagnostic_dir: str | Path) -> dict[str, Any]:
    root = Path(diagnostic_dir)
    snapshot_meta_path = root / "snapshot_meta.json"
    decision_path = root / "decision.json"
    summary_path = root / "summary.md"

    context: dict[str, Any] = {"diagnostic_dir": str(root)}
    if snapshot_meta_path.exists():
        snapshot_meta = _read_json(snapshot_meta_path)
        context["snapshot_meta"] = {
            "exchange": snapshot_meta.get("exchange"),
            "symbol": snapshot_meta.get("symbol"),
            "timeframe_main": snapshot_meta.get("timeframe_main"),
            "timeframe_sub": snapshot_meta.get("timeframe_sub"),
            "asof": snapshot_meta.get("asof"),
            "data_quality": snapshot_meta.get("data_quality"),
            "market_state": snapshot_meta.get("market_state"),
            "counts": snapshot_meta.get("counts"),
            "tail": snapshot_meta.get("tail"),
        }
    if decision_path.exists():
        context["decision"] = _read_json(decision_path)
    if summary_path.exists():
        context["summary_md"] = summary_path.read_text(encoding="utf-8")
    return context


def build_review_system_prompt() -> str:
    return (
        "你是一个保守的缠论视觉复核助手。"
        "你会同时参考截图和结构化诊断数据，对当前信号是否可信给出复核结论。"
        "不要承诺收益，不要自动下单。"
        "输出必须是 JSON，且字段固定为 decision/agreement/confidence/reasons/risks/cn_summary。"
        "其中 decision 只能是 accept/reject/needs_review，agreement 只能是 high/medium/low。"
        "reasons 和 risks 必须是字符串数组，confidence 必须是 0 到 1 的数字。"
    )


def build_review_user_prompt(
    *,
    diagnostic_context: dict[str, Any],
    image_paths: Sequence[str | Path],
    extra_instruction: str = "",
) -> str:
    image_labels = [str(Path(path)) for path in image_paths]
    compact_context = json.dumps(diagnostic_context, ensure_ascii=False, indent=2)

    parts = [
        "请基于以下缠论结构诊断信息和附带截图做复核。",
        "重点判断：",
        "1. 当前规则引擎给出的 action 与 signals 是否有明显结构性问题。",
        "2. 截图上是否存在规则引擎可能漏掉的主次级别冲突、假突破、未确认三类点、背驰不足等问题。",
        "3. 如果图像和结构化数据冲突，优先指出冲突点，不要强行给高置信度结论。",
        "",
        f"附带图片: {image_labels if image_labels else '无'}",
        "",
        "结构化上下文：",
        compact_context,
    ]
    if extra_instruction:
        parts.extend(["", f"额外要求：{extra_instruction}"])
    return "\n".join(parts)


def build_review_payload(
    *,
    model: str,
    diagnostic_context: dict[str, Any],
    image_paths: Sequence[str | Path],
    extra_instruction: str = "",
    temperature: float = 0.1,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    return build_chat_completion_payload(
        model=model,
        system_prompt=build_review_system_prompt(),
        user_text=build_review_user_prompt(
            diagnostic_context=diagnostic_context,
            image_paths=image_paths,
            extra_instruction=extra_instruction,
        ),
        image_paths=image_paths,
        temperature=temperature,
        max_tokens=max_tokens,
    )
