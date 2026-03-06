from __future__ import annotations
# ruff: noqa: E402

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from _script_utils import ensure_src_on_path

ensure_src_on_path()

from ai_trader.llm import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    build_review_payload,
    create_chat_completion,
    extract_assistant_text,
    load_diagnostic_context,
    load_openai_compat_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Chan visual review via OpenAI-compatible chat/completions")
    parser.add_argument("--diagnostic-dir", required=True, help="包含 snapshot_meta.json / decision.json 的诊断目录")
    parser.add_argument("--image", action="append", default=[], help="附带给视觉模型的图像路径，可重复传入")
    parser.add_argument("--model", default=DEFAULT_OPENAI_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_OPENAI_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--extra-instruction", default="", help="附加给模型的额外中文要求")
    parser.add_argument("--output-root", default="outputs/vision_reviews")
    return parser.parse_args()


def _request_meta(args: argparse.Namespace, payload: dict, image_paths: list[Path]) -> dict:
    return {
        "diagnostic_dir": args.diagnostic_dir,
        "image_paths": [str(path) for path in image_paths],
        "base_url": args.base_url,
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "message_count": len(payload.get("messages", [])),
    }


def main() -> None:
    args = parse_args()
    image_paths = [Path(path) for path in args.image]
    missing_images = [str(path) for path in image_paths if not path.exists()]
    if missing_images:
        raise FileNotFoundError(f"以下图像不存在: {missing_images}")

    diagnostic_context = load_diagnostic_context(args.diagnostic_dir)
    if "snapshot_meta" not in diagnostic_context and "decision" not in diagnostic_context:
        raise FileNotFoundError("诊断目录内未找到 snapshot_meta.json 或 decision.json")

    config = load_openai_compat_config(
        base_url=args.base_url,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
    )
    payload = build_review_payload(
        model=config.model,
        diagnostic_context=diagnostic_context,
        image_paths=image_paths,
        extra_instruction=args.extra_instruction,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )
    response_payload = create_chat_completion(config, payload)
    assistant_text = extract_assistant_text(response_payload)

    snapshot_meta = diagnostic_context.get("snapshot_meta", {})
    symbol = snapshot_meta.get("symbol") or diagnostic_context.get("decision", {}).get("symbol") or "unknown"
    timeframe_main = snapshot_meta.get("timeframe_main") or diagnostic_context.get("decision", {}).get("timeframe_main") or "na"
    timeframe_sub = snapshot_meta.get("timeframe_sub") or diagnostic_context.get("decision", {}).get("timeframe_sub") or "na"

    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    symbol_key = str(symbol).replace("/", "")
    out_dir = Path(args.output_root) / f"{symbol_key}_{timeframe_main}_{timeframe_sub}" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    review_input = {
        "diagnostic_context": diagnostic_context,
        "request_meta": _request_meta(args, payload, image_paths),
    }
    (out_dir / "review_input.json").write_text(json.dumps(review_input, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "review_response.json").write_text(json.dumps(response_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_lines = [
        "# 缠论视觉复核结果",
        "",
        f"- symbol: {symbol}",
        f"- timeframe: {timeframe_main}/{timeframe_sub}",
        f"- model: {config.model}",
        f"- base_url: {config.base_url}",
        f"- diagnostic_dir: {args.diagnostic_dir}",
        f"- images: {[str(path) for path in image_paths] if image_paths else '[]'}",
        "",
        "## Assistant Output",
        "",
        assistant_text or "(empty)",
    ]
    (out_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"Vision review completed. Output: {out_dir}")


if __name__ == "__main__":
    main()
