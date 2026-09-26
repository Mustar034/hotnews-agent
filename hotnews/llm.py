"""DeepSeek LLM 调用封装。"""
import json

import requests

from .config import API_KEY, BASE_URL, MODEL, REQUEST_TIMEOUT


def call_llm(system: str, user: str, temperature: float = 0.3) -> str:
    """一次 DeepSeek 调用。requests 直连 OpenAI 兼容接口，无额外 SDK 依赖。"""
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "stream": False,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_json_llm(raw: str, fallback):
    """容错解析 LLM 输出的 JSON（剥掉 ```json 代码块标记等）。"""
    raw = (raw or "").strip()
    for token in ("```json", "```", "```JSON"):
        if raw.startswith(token):
            raw = raw[len(token):].strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback
