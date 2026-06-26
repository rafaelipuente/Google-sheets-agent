"""Provider-agnostic interface for a tool-calling model.

Message dicts follow the OpenAI chat format (role/content/tool_calls/
tool_call_id). A non-OpenAI adapter is responsible for translating to and from
that shape so the agent loop stays unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as emitted by the model


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The assistant message to append to history verbatim (provider-shaped).
    assistant_message: dict = field(default_factory=dict)
    # Observability: model name, token usage, and call latency.
    model: str | None = None
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0


class ToolCallingModel(ABC):
    @abstractmethod
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        """Run one completion with tool access and return a normalized response."""
        raise NotImplementedError
