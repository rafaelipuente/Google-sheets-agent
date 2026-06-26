"""OpenAI tool-calling adapter (v1 default)."""

from __future__ import annotations

from .. import config
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
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
        )
        message = response.choices[0].message
        tool_calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (message.tool_calls or [])
        ]
        return ModelResponse(
            content=message.content,
            tool_calls=tool_calls,
            assistant_message=message.model_dump(exclude_none=True),
        )
