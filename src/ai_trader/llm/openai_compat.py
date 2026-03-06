from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib import error, request

from dotenv import load_dotenv

DEFAULT_OPENAI_BASE_URL = "https://www.packyapi.com"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_TIMEOUT_SECONDS = 90.0


@dataclass(slots=True)
class OpenAICompatConfig:
    api_key: str
    base_url: str = DEFAULT_OPENAI_BASE_URL
    model: str = DEFAULT_OPENAI_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def chat_completions_url(self) -> str:
        return f"{normalize_base_url(self.base_url)}/chat/completions"


def normalize_base_url(base_url: str) -> str:
    text = base_url.strip().rstrip("/")
    if not text:
        raise ValueError("base_url 不能为空")
    return text if text.endswith("/v1") else f"{text}/v1"


def load_openai_compat_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> OpenAICompatConfig:
    load_dotenv()

    resolved_api_key = api_key or os.getenv("AI_TRADER_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not resolved_api_key:
        raise ValueError("缺少 API key，请设置 AI_TRADER_OPENAI_API_KEY 或 OPENAI_API_KEY")

    resolved_base_url = base_url or os.getenv("AI_TRADER_OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL
    resolved_model = model or os.getenv("AI_TRADER_OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    return OpenAICompatConfig(
        api_key=resolved_api_key,
        base_url=resolved_base_url,
        model=resolved_model,
        timeout_seconds=timeout_seconds,
    )


def image_path_to_data_url(path: str | Path) -> str:
    image_path = Path(path)
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_chat_completion_payload(
    *,
    model: str,
    system_prompt: str,
    user_text: str,
    image_paths: Sequence[str | Path] = (),
    temperature: float = 0.1,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    user_content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for path in image_paths:
        user_content.append({"type": "image_url", "image_url": {"url": image_path_to_data_url(path)}})

    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


def create_chat_completion(config: OpenAICompatConfig, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=config.chat_completions_url,
        data=body,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"chat/completions 请求失败: HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"chat/completions 网络请求失败: {exc.reason}") from exc

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"chat/completions 返回了非 JSON 内容: {response_body[:500]}") from exc


def extract_assistant_text(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part).strip()
    return ""
