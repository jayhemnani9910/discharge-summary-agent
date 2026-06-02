"""DeepSeek implementation of the chat/reasoning provider.

DeepSeek exposes an OpenAI-compatible Chat Completions API with function calling, so
this provider translates our provider-agnostic message list into OpenAI-format messages
and tools, calls the endpoint over plain HTTP (no extra dependency), and parses the
tool calls back. DeepSeek has no vision model, so ``transcribe`` raises: transcription
must come from a vision provider or a pre-extracted transcript.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import (
    FatalLLMError,
    LLMProvider,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    TransientLLMError,
)

_TRANSIENT_CODES = {408, 409, 429, 500, 502, 503, 504}


def _to_openai_schema(schema):
    """Convert our Gemini-style (uppercase type) JSON schema to standard lowercase."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.lower()
        elif k == "properties" and isinstance(v, dict):
            out[k] = {pk: _to_openai_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _to_openai_schema(v)
        else:
            out[k] = v
    return out


class DeepSeekProvider(LLMProvider):
    def __init__(self, config):
        self.config = config
        self.endpoint = config.deepseek_base_url.rstrip("/") + "/chat/completions"

    # --- public API ---------------------------------------------------------
    def chat(self, system: str, history: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        body = {
            "model": self.config.deepseek_model,
            "messages": self._to_openai_messages(system, history),
            "tools": [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description,
                    "parameters": _to_openai_schema(t.parameters)}}
                for t in tools
            ],
            "tool_choice": "auto",
            "temperature": 0.0,
        }
        data = self._post(body)
        return self._parse(data)

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        body = {
            "model": self.config.deepseek_model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post(body)
        return (data["choices"][0]["message"].get("content") or "").strip()

    def transcribe(self, image_bytes: bytes, mime_type: str, prompt: str) -> str:
        raise FatalLLMError("DeepSeek has no vision model; supply a vision provider or "
                            "a pre-extracted transcript (--transcript).")

    # --- internals ----------------------------------------------------------
    def _post(self, body: dict) -> dict:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.config.deepseek_api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.config.llm_timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in _TRANSIENT_CODES:
                raise TransientLLMError(f"DeepSeek {exc.code}: {detail}")
            raise FatalLLMError(f"DeepSeek {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            # Network/timeout: retryable.
            raise TransientLLMError(f"DeepSeek network error: {exc}")

    def _parse(self, data: dict) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        text = msg.get("content") or ""
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(name=fn.get("name", ""), args=args, id=tc.get("id")))
        return LLMResponse(text=text, tool_calls=calls, stop_reason=str(choice.get("finish_reason") or ""))

    def _to_openai_messages(self, system: str, history: list[Message]) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            kind = m["kind"]
            if kind == "text":
                msgs.append({"role": "user", "content": m["text"]})
            elif kind == "model_turn":
                entry: dict = {"role": "assistant", "content": m.get("text") or ""}
                if m.get("calls"):
                    entry["tool_calls"] = [
                        {"id": c.get("id") or f"call_{i}", "type": "function",
                         "function": {"name": c["name"],
                                      "arguments": json.dumps(c.get("args") or {})}}
                        for i, c in enumerate(m["calls"])
                    ]
                msgs.append(entry)
            elif kind == "tool_results":
                for i, r in enumerate(m["results"]):
                    msgs.append({"role": "tool",
                                 "tool_call_id": r.get("id") or f"call_{i}",
                                 "content": json.dumps(r["result"], ensure_ascii=False)})
            else:
                raise ValueError(f"Unknown message kind: {kind!r}")
        return msgs
