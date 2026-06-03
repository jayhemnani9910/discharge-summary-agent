"""Gemini provider resilience: multiple API keys and a model fallback list, rotating on
quota (429) errors. No network: the SDK client is faked."""

import discharge_agent.llm.gemini as gem
from discharge_agent.config import Config


class _Part:
    def __init__(self, text):
        self.text = text
        self.function_call = None


class _Cand:
    def __init__(self, text):
        self.content = type("C", (), {"parts": [_Part(text)]})()
        self.finish_reason = "STOP"


class _Resp:
    def __init__(self, text):
        self.candidates = [_Cand(text)]


def _patch_client(monkeypatch, behavior):
    """behavior(api_key, model) -> str text, or raises."""
    def fake_client(api_key=None, **kw):
        class _Models:
            def generate_content(self, model, contents, config):
                return _Resp(behavior(api_key, model))
        return type("Client", (), {"models": _Models()})()
    monkeypatch.setattr(gem.genai, "Client", fake_client)


def _cfg(**over):
    cfg = Config()
    cfg.vision_provider = "gemini"
    cfg.vision_min_interval_seconds = 0.0
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def test_gemini_rotates_keys_on_quota(monkeypatch):
    seen = []

    def behavior(api_key, model):
        seen.append(api_key)
        if api_key == "k0":
            raise Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
        return "page text"

    _patch_client(monkeypatch, behavior)
    cfg = _cfg(gemini_api_keys=["k0", "k1"], gemini_vision_models=["gemini-2.5-flash"])
    out = gem.GeminiProvider(cfg).transcribe(b"img", "image/png", "prompt")
    assert out == "page text"
    assert seen == ["k0", "k1"]   # exhausted k0, rotated to k1


def test_gemini_falls_back_across_models(monkeypatch):
    seen = []

    def behavior(api_key, model):
        seen.append((api_key, model))
        if model == "model-a":
            raise Exception("429 RESOURCE_EXHAUSTED")   # every key is out for model-a
        return "ok"

    _patch_client(monkeypatch, behavior)
    cfg = _cfg(gemini_api_keys=["k0", "k1"], gemini_vision_models=["model-a", "model-b"])
    out = gem.GeminiProvider(cfg).transcribe(b"img", "image/png", "prompt")
    assert out == "ok"
    assert ("k0", "model-b") in seen   # fell back to model-b after model-a was exhausted on all keys


def test_single_gemini_api_key_still_works(monkeypatch):
    _patch_client(monkeypatch, lambda api_key, model: "single-key text")
    cfg = _cfg(gemini_api_key="only", gemini_api_keys=[], gemini_vision_models=["gemini-2.5-flash"])
    out = gem.GeminiProvider(cfg).transcribe(b"img", "image/png", "prompt")
    assert out == "single-key text"


def test_gemini_client_uses_configured_timeout(monkeypatch):
    # The configured llm_timeout must be applied to the Gemini client (it previously was not,
    # so a stalled Gemini call could hang the run forever).
    captured = {}

    def fake_client(api_key=None, http_options=None, **kw):
        captured["http_options"] = http_options
        return type("C", (), {"models": None})()

    monkeypatch.setattr(gem.genai, "Client", fake_client)
    cfg = _cfg(gemini_api_keys=["k"], llm_timeout_seconds=42.0)
    gem.GeminiProvider(cfg)._client(0)
    assert getattr(captured["http_options"], "timeout", None) == 42000   # ms


def test_default_vision_model_is_not_the_zero_quota_one(monkeypatch):
    # The shipped CODE default must not be gemini-2.0-flash (free-tier limit: 0). Clear the
    # local .env overrides so we test the default baked into config.py, not the dev's .env.
    for var in ("GEMINI_VISION_MODEL", "GEMINI_VISION_MODELS", "GEMINI_CHAT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config()
    assert cfg.gemini_vision_model != "gemini-2.0-flash"
    assert "gemini-2.0-flash" not in cfg.gemini_vision_models
