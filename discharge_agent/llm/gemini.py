"""Gemini implementation of the LLM provider.

Handles the two capabilities the agent needs (tool-calling chat and image
transcription), translates the provider-agnostic message list into Gemini
``Content`` objects, classifies failures into transient vs fatal, and retries
transient failures with exponential backoff (Hard requirement #8).
"""

from __future__ import annotations

import time

from google import genai
from google.genai import errors, types

from .base import (
    FatalLLMError,
    LLMProvider,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    TransientLLMError,
)

# HTTP status codes worth retrying: rate limit + transient server errors.
_TRANSIENT_CODES = {408, 409, 429, 500, 502, 503, 504}


def _classify(exc: Exception) -> Exception:
    """Map a raw SDK/network exception onto our transient/fatal contract."""
    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        if code in _TRANSIENT_CODES:
            return TransientLLMError(f"Gemini {code}: {exc}")
        return FatalLLMError(f"Gemini {code}: {exc}")
    # Network-level problems (timeouts, dropped connections) are worth a retry.
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "transport", "remoteprotocol")):
        return TransientLLMError(f"network: {exc}")
    return FatalLLMError(f"unexpected: {exc}")


def _is_quota(exc: Exception) -> bool:
    """A rate-limit / quota error (429), worth rotating to another key or model."""
    s = str(exc)
    return ("429" in s) or ("RESOURCE_EXHAUSTED" in s) or ("quota" in s.lower())


class GeminiProvider(LLMProvider):
    def __init__(self, config):
        self.config = config
        self._keys = (list(config.gemini_api_keys)
                      or ([config.gemini_api_key] if config.gemini_api_key else []))
        self._vision_models = list(config.gemini_vision_models) or [config.gemini_vision_model]
        self._chat_models = list(config.gemini_chat_models) or [config.gemini_chat_model]
        self._clients: dict = {}
        self._last_vision_call = 0.0

    def _client(self, idx: int):
        c = self._clients.get(idx)
        if c is None:
            # Apply the configured timeout to the Gemini client (in ms) so a stalled call cannot
            # hang the run forever; per-call retries still live in retry.call_with_retries.
            c = genai.Client(
                api_key=self._keys[idx],
                http_options=types.HttpOptions(timeout=int(self.config.llm_timeout_seconds * 1000)))
            self._clients[idx] = c
        return c

    # --- public API ---------------------------------------------------------
    def chat(self, system: str, history: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        contents = self._to_contents(history)
        tool_decls = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=t.name, description=t.description, parameters=t.parameters
                    )
                    for t in tools
                ]
            )
        ]
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            tools=tool_decls,
            temperature=0.0,
            # Manual function calling: we dispatch tools ourselves so every step is
            # observable and the control cap is enforced by our loop, not the SDK.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )

        resp = self._generate(self._chat_models, contents, cfg)
        return self._parse_response(resp)

    def transcribe(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        self._respect_vision_rate_limit()
        contents = [types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    types.Part.from_text(text=prompt)]
        cfg = types.GenerateContentConfig(temperature=0.0)
        resp = self._generate(self._vision_models, contents, cfg)
        text = self._extract_text(resp)
        if not text.strip():
            # An empty transcription is treated as a (transient) failure rather than a
            # silent success, so the caller's retry/flag logic engages.
            raise TransientLLMError("vision model returned empty text")
        return text

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            response_mime_type="application/json" if json_mode else None,
        )
        resp = self._generate(self._chat_models, [prompt], cfg)
        return self._extract_text(resp)

    # --- internals ----------------------------------------------------------
    def _generate(self, models, contents, cfg):
        """Run one generation, rotating across keys and the model fallback list on a quota
        (429) error so a single exhausted key/model does not fail the call. A non-quota error
        is classified and raised at once. If every key/model is quota-exhausted, the last quota
        error is raised as transient so retry.call_with_retries can still back off and retry.

        Per-call retries of transient/network errors live in retry.call_with_retries so they
        stay visible in the trace; this method only handles key/model rotation.
        """
        model_list = list(models) if isinstance(models, (list, tuple)) else [models]
        if not self._keys:
            raise FatalLLMError(
                "no Gemini API key configured (set GEMINI_API_KEY or GEMINI_API_KEYS)")
        last_quota = None
        for model in model_list:
            for ki in range(len(self._keys)):
                try:
                    return self._client(ki).models.generate_content(
                        model=model, contents=contents, config=cfg)
                except Exception as raw:  # noqa: BLE001 - re-classified below
                    if _is_quota(raw):
                        last_quota = TransientLLMError(f"Gemini quota (model={model}): {raw}")
                        continue
                    raise _classify(raw)
        raise last_quota or TransientLLMError("Gemini: all keys/models exhausted")

    def _respect_vision_rate_limit(self) -> None:
        gap = time.monotonic() - self._last_vision_call
        wait = self.config.vision_min_interval_seconds - gap
        if wait > 0:
            time.sleep(wait)
        self._last_vision_call = time.monotonic()

    @staticmethod
    def _extract_text(resp) -> str:
        parts = []
        for cand in getattr(resp, "candidates", None) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    parts.append(part.text)
        return "\n".join(parts)

    def _parse_response(self, resp) -> LLMResponse:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    args = dict(fc.args) if fc.args else {}
                    calls.append(ToolCall(name=fc.name, args=args, id=getattr(fc, "id", None)))
                elif getattr(part, "text", None):
                    text_parts.append(part.text)
        stop = ""
        if candidates:
            stop = str(getattr(candidates[0], "finish_reason", "") or "")
        return LLMResponse(text="\n".join(text_parts), tool_calls=calls, stop_reason=stop)

    def _to_contents(self, history: list[Message]) -> list[types.Content]:
        """Translate our message dicts into Gemini Content objects.

        Consecutive same-role messages are merged into one Content so the
        conversation always alternates user/model, which Gemini expects.
        """
        contents: list[types.Content] = []
        for msg in history:
            role = "model" if msg["role"] == "model" else "user"
            parts = self._parts_for(msg)
            if contents and contents[-1].role == role:
                contents[-1].parts.extend(parts)
            else:
                contents.append(types.Content(role=role, parts=parts))
        return contents

    @staticmethod
    def _parts_for(msg: Message) -> list[types.Part]:
        kind = msg["kind"]
        if kind == "text":
            return [types.Part.from_text(text=msg["text"])]
        if kind == "model_turn":
            parts = []
            if msg.get("text"):
                parts.append(types.Part.from_text(text=msg["text"]))
            for c in msg.get("calls", []):
                parts.append(types.Part.from_function_call(name=c["name"], args=c.get("args") or {}))
            return parts or [types.Part.from_text(text="(no content)")]
        if kind == "tool_results":
            return [
                types.Part.from_function_response(
                    name=r["name"], response=_as_response_dict(r["result"])
                )
                for r in msg["results"]
            ]
        raise ValueError(f"Unknown message kind: {kind!r}")


def _as_response_dict(result) -> dict:
    """Gemini requires a dict for a function response; wrap non-dicts."""
    if isinstance(result, dict):
        return result
    return {"result": result}
