"""Run configuration.

All tunable knobs live here so the agent's behaviour is auditable from one place.
Values come from environment variables (loaded from a local .env) with safe defaults.

The system separates two LLM roles:
* a VISION provider that transcribes the scanned pages (only Gemini here; DeepSeek has
  no vision), and
* a CHAT provider that runs the reasoning loop and the verifier (Gemini or DeepSeek).
They can be the same or different. When a pre-extracted transcript is supplied, no
vision provider is needed at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv(override=False)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _csv(name: str) -> list:
    """Parse a comma-separated env var into a list of non-empty, stripped values."""
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


@dataclass
class Config:
    # --- Provider selection -------------------------------------------------
    # CHAT_PROVIDER / VISION_PROVIDER each one of: gemini | deepseek | mock.
    chat_provider: str = field(
        default_factory=lambda: os.environ.get("CHAT_PROVIDER", os.environ.get("LLM_PROVIDER", "gemini")))
    vision_provider: str = field(default_factory=lambda: os.environ.get("VISION_PROVIDER", "gemini"))

    # Gemini (vision and/or chat). Multiple keys and a model fallback list are supported so a
    # 429 (quota) on one key/model rotates to the next instead of failing the page. Defaults are
    # gemini-2.5-flash (gemini-2.0-flash has free-tier limit:0 and must not be the default).
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_api_keys: list = field(
        default_factory=lambda: _csv("GEMINI_API_KEYS") or _csv("GEMINI_API_KEY"))
    gemini_chat_model: str = field(
        default_factory=lambda: os.environ.get("GEMINI_CHAT_MODEL", "gemini-2.5-flash"))
    gemini_vision_model: str = field(
        default_factory=lambda: os.environ.get("GEMINI_VISION_MODEL", "gemini-2.5-flash"))
    gemini_chat_models: list = field(
        default_factory=lambda: _csv("GEMINI_CHAT_MODELS")
        or [os.environ.get("GEMINI_CHAT_MODEL", "gemini-2.5-flash")])
    gemini_vision_models: list = field(
        default_factory=lambda: _csv("GEMINI_VISION_MODELS")
        or [os.environ.get("GEMINI_VISION_MODEL", "gemini-2.5-flash")])

    # DeepSeek (chat only; OpenAI-compatible API).
    deepseek_api_key: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", ""))
    deepseek_model: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    deepseek_base_url: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))

    # --- Control caps (Hard requirement #9: the agent cannot run forever) ---
    max_steps: int = field(default_factory=lambda: _int("AGENT_MAX_STEPS", 120))
    max_consecutive_tool_errors: int = field(default_factory=lambda: _int("AGENT_MAX_TOOL_ERRORS", 8))

    # --- Failure handling (Hard requirement #8) -----------------------------
    llm_max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 4))
    llm_backoff_base_seconds: float = 2.0
    llm_timeout_seconds: float = 120.0

    # Spacing between vision calls during the (one-time, cached) ingestion pass.
    vision_min_interval_seconds: float = field(
        default_factory=lambda: float(os.environ.get("VISION_MIN_INTERVAL", "1.0")))

    # --- Rendering ----------------------------------------------------------
    render_long_edge_px: int = 1600

    # --- Demonstration hook -------------------------------------------------
    inject_read_failure_page: int | None = field(
        default_factory=lambda: (_int("INJECT_READ_FAILURE_PAGE", 0) or None))

    # --- validation ---------------------------------------------------------
    def require_keys(self, need_vision: bool) -> None:
        missing = []
        if self.chat_provider == "gemini" and not (self.gemini_api_keys or self.gemini_api_key):
            missing.append("GEMINI_API_KEY (chat)")
        if self.chat_provider == "deepseek" and not self.deepseek_api_key:
            missing.append("DEEPSEEK_API_KEY (chat)")
        if (need_vision and self.vision_provider == "gemini"
                and not (self.gemini_api_keys or self.gemini_api_key)):
            missing.append("GEMINI_API_KEY (vision)")
        if need_vision and self.vision_provider == "deepseek":
            raise SystemExit("DeepSeek has no vision model; use a vision provider or supply "
                             "--transcript with a pre-extracted transcript.")
        if missing:
            raise SystemExit("Missing credentials: " + ", ".join(missing)
                             + ". Copy .env.example to .env and fill them in.")
