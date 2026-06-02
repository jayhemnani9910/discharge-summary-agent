"""Centralised retry/backoff for LLM and tool calls (Hard requirement #8).

Keeping retries here (rather than hidden inside the provider) means every retry is
visible in the trace and both the agent loop and the ingestion pass share one policy.
Transient errors back off exponentially up to a cap; fatal errors propagate at once.
A call that exhausts its retries raises, so the caller decides whether to fall back or
flag. Nothing here ever turns a failure into a fake success.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from .llm.base import TransientLLMError

T = TypeVar("T")


def call_with_retries(fn: Callable[[], T], *, config, tracer=None, what: str = "llm") -> T:
    attempt = 0
    while True:
        try:
            return fn()
        except TransientLLMError as exc:
            if attempt >= config.llm_max_retries:
                if tracer:
                    tracer.emit("retry", what=what, attempt=attempt + 1, error=str(exc),
                                outcome="gave_up")
                raise
            if tracer:
                tracer.emit("retry", what=what, attempt=attempt + 1, error=str(exc),
                            outcome="backoff")
            time.sleep(config.llm_backoff_base_seconds * (2 ** attempt))
            attempt += 1
