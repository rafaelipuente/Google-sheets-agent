"""OpenAI tool-calling adapter (v1 default)."""

from __future__ import annotations

import time

from .. import config
from ..retry import with_retry
from .base import ModelResponse, ToolCall, ToolCallingModel


class OpenAIModel(ToolCallingModel):
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.OPENAI_MODEL
        self._api_key = api_key or config.OPENAI_API_KEY
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI

            if not self._api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set; cannot run the agent loop."
                )
            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        client = self._ensure_client()
        start = time.perf_counter()
        # Retry transient connection drops; openai's own 4xx errors are not retried.
        response = with_retry(
            lambda: client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
            ),
            label="OpenAI chat completion",
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        message = response.choices[0].message
        tool_calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (message.tool_calls or [])
        ]
        usage = {}
        if getattr(response, "usage", None) is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return ModelResponse(
            content=message.content,
            tool_calls=tool_calls,
            assistant_message=message.model_dump(exclude_none=True),
            model=self.model,
            usage=usage,
            latency_ms=latency_ms,
        )
