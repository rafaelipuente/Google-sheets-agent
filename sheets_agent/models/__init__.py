"""Swappable tool-calling model adapters.

The agent loop depends on the ``ToolCallingModel`` interface, not on any one
provider. v1 ships ``OpenAIModel``; swap by passing a different implementation.
"""

from .base import ModelResponse, ToolCall, ToolCallingModel
from .openai_model import OpenAIModel

__all__ = ["ModelResponse", "ToolCall", "ToolCallingModel", "OpenAIModel"]
