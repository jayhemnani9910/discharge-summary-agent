"""Deterministic, offline LLM provider for tests and no-API demos.

It never calls a network. Two ways to drive it:

* ``script``: a list consumed one entry per ``chat`` call. Each entry is either an
  ``LLMResponse`` (returned as-is) or an ``Exception`` (raised, to exercise the
  retry/failure paths).
* ``responder``: a callable ``(system, history, tools) -> LLMResponse`` that can
  inspect the running conversation and react (e.g. retry after a tool error).

Transcription is served from ``transcriber`` (a callable) or a static string.
"""

from __future__ import annotations

from typing import Callable

from .base import LLMProvider, LLMResponse, Message, ToolSpec, TransientLLMError


class MockProvider(LLMProvider):
    def __init__(
        self,
        config=None,
        *,
        script: list | None = None,
        responder: Callable[[str, list[Message], list[ToolSpec]], LLMResponse] | None = None,
        transcriber: Callable[[bytes, str, str], str] | str | None = None,
        completer: Callable[[str, str, bool], str] | str | None = None,
    ):
        self.config = config
        self._script = list(script) if script is not None else None
        self._responder = responder
        self._transcriber = transcriber
        self._completer = completer
        self.chat_calls = 0
        self.transcribe_calls = 0
        self.complete_calls = 0

    def chat(self, system: str, history: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.chat_calls += 1
        if self._script is not None:
            if not self._script:
                raise AssertionError("MockProvider script exhausted but chat() was called again")
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self._responder is not None:
            return self._responder(system, history, tools)
        raise AssertionError("MockProvider needs a script or responder")

    def transcribe(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        self.transcribe_calls += 1
        if callable(self._transcriber):
            return self._transcriber(image_bytes, mime_type, prompt)
        if isinstance(self._transcriber, str):
            return self._transcriber
        raise TransientLLMError("MockProvider has no transcriber configured")

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        self.complete_calls += 1
        if callable(self._completer):
            return self._completer(system, prompt, json_mode)
        if isinstance(self._completer, str):
            return self._completer
        # Default: approve nothing/everything explicitly must be configured by the test.
        raise AssertionError("MockProvider has no completer configured")
