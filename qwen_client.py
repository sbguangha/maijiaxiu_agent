"""阿里云百炼 OpenAI 兼容接口。文本用 qwen3.7-plus，看图用 qwen3.8-omni-flash。"""
from __future__ import annotations

import logging
from typing import Any

import requests

from config import settings

logger = logging.getLogger("qwen-client")


def dashscope_configured() -> bool:
    return bool(settings.llm.dashscope_api_key)


def qwen_chat(
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float = 0.4,
    timeout: int = 120,
) -> str:
    """调用百炼 Chat Completions，关闭思考模式，只取文本。"""
    api_key = settings.llm.dashscope_api_key
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")

    url = settings.llm.dashscope_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "enable_thinking": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"Qwen 返回非 JSON，HTTP {response.status_code}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"Qwen 调用失败 HTTP {response.status_code}: {data}")

    message = (data.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        content = "".join(parts)
    text = str(content).strip()
    if not text:
        raise RuntimeError(f"Qwen 返回空内容: {data}")
    logger.info("Qwen %s 返回 %d 字", model, len(text))
    return text
