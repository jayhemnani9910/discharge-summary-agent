"""Provider-agnostic LLM interface and the message format the agent loop speaks.

Design choice: the conversation is a list of plain dicts (``Message``) rather than
provider-native objects. This keeps the agent loop independent of any vendor, makes
the whole transcript JSON-serialisable for the trace/replay, and lets the mock
provider read exactly the same history the real one does.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# --- Errors -----------------------------------------------------------------
class LLMError(Exception):
    """Base class for provider errors."""


class TransientLLMError(LLMError):
    """Retryable: rate limit (429), timeout, or a 5xx. The caller backs off and retries."""


class FatalLLMError(LLMError):
    """Non-retryable: bad request, auth failure, etc. Retrying will not help."""


# --- Tool + response value objects -----------------------------------------
@dataclass
class ToolSpec:
    """A tool the model may call. ``parameters`` is a JSON-schema dict."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass
class LLMResponse:
    """One model turn: any natural-language text it produced plus any tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""


# --- Message constructors ---------------------------------------------------
# A Message is a dict with a "role" ("user" or "model") and a "kind".
Message = dict[str, Any]


def msg_user_text(text: str) -> Message:
    return {"role": "user", "kind": "text", "text": text}


def msg_model_turn(text: str, calls: list[ToolCall]) -> Message:
    """One assistant turn: optional reasoning text plus any tool calls it made.

    Combining text and tool calls in a single message keeps the history valid for both
    Gemini (alternating roles) and OpenAI-style APIs (tool results must follow the
    assistant message that requested them).
    """
    return {
        "role": "model",
        "kind": "model_turn",
        "text": text or "",
        "calls": [{"name": c.name, "args": c.args, "id": c.id} for c in calls],
    }


def msg_tool_results(results: list[dict[str, Any]]) -> Message:
    """results: list of {"name": str, "id": str|None, "result": dict}."""
    return {"role": "user", "kind": "tool_results", "results": results}


# --- Provider interface -----------------------------------------------------
class LLMProvider(ABC):
    """Two capabilities the system needs: tool-calling chat, and image transcription."""

    @abstractmethod
    def chat(self, system: str, history: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        """One reasoning step. Returns the model's text and/or tool calls.

        Implementations must raise ``TransientLLMError`` for retryable failures and
        ``FatalLLMError`` otherwise. They must never silently return an empty success
        on failure (Hard requirement #8).
        """

    @abstractmethod
    def transcribe(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        """Transcribe a single page image to text. Same error contract as ``chat``."""

    @abstractmethod
    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Single-shot text completion (no tools). Used by the post-hoc verifier."""
