"""LLM provider layer — all supported vendors expose an OpenAI-compatible API.

- deepseek: https://api.deepseek.com        (default model: deepseek-chat)
- qwen:     DashScope compatible-mode       (default model: qwen-plus)
- glm:      Zhipu BigModel                  (default model: glm-4-plus)

Switch via env: LLM_PROVIDER + LLM_MODEL + the matching *_API_KEY.
"""

from __future__ import annotations

from typing import Any, Optional

from openai import OpenAI

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
    },
}


class LLM:
    def __init__(self, provider: str, api_key: str, model: str = ""):
        if provider not in PROVIDERS:
            raise ValueError(
                f"unknown LLM_PROVIDER {provider!r}; "
                f"expected one of {sorted(PROVIDERS)}"
            )
        if not api_key:
            raise ValueError(f"missing API key for provider {provider!r}")
        spec = PROVIDERS[provider]
        self.provider = provider
        self.model = model or spec["default_model"]
        self.client = OpenAI(base_url=spec["base_url"], api_key=api_key)

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Any:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self.client.chat.completions.create(**kwargs)
