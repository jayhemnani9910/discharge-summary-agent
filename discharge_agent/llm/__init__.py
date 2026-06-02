"""LLM provider abstraction.

The agent loop is written against the provider-agnostic interface in ``base.py``.
``gemini.py`` is the real implementation; ``mock.py`` is a deterministic, offline
implementation used by the tests and by the no-API demo mode.
"""

from .base import (
    LLMProvider,
    LLMResponse,
    ToolCall,
    ToolSpec,
    LLMError,
    TransientLLMError,
    FatalLLMError,
    msg_user_text,
    msg_model_turn,
    msg_tool_results,
)

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "ToolSpec",
    "LLMError",
    "TransientLLMError",
    "FatalLLMError",
    "msg_user_text",
    "msg_model_turn",
    "msg_tool_results",
    "build_provider",
]


def build_provider(name: str, config):
    """Factory: return a provider instance by name ('gemini' | 'deepseek' | 'mock')."""
    if name == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(config)
    if name == "deepseek":
        from .deepseek import DeepSeekProvider

        return DeepSeekProvider(config)
    if name == "mock":
        from .mock import MockProvider

        return MockProvider(config)
    raise ValueError(f"Unknown LLM provider: {name!r}")
