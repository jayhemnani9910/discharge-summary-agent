"""Ingestion: transcription parsing, the unreadable-page path, and retry-on-failure."""

import json

import discharge_agent.ingest as ingest
from discharge_agent.config import Config
from discharge_agent.ingest import PageRecord, PageStore, _parse_transcription, _transcribe_one
from discharge_agent.llm.mock import MockProvider


def test_parse_plain_json():
    out = _parse_transcription('{"text":"hello","doc_type":"note","dates":[],"gist":"g"}')
    assert out["text"] == "hello" and out["doc_type"] == "note"


def test_parse_fenced_json():
    out = _parse_transcription('```json\n{"text":"hi","gist":"g"}\n```')
    assert out["text"] == "hi"


def test_parse_falls_back_to_raw_text():
    out = _parse_transcription("not json at all")
    assert out["text"] == "not json at all"


def test_store_search_and_index(store):
    hits = store.search("gastroenteritis")
    assert hits and hits[0]["page"] == 1
    assert "UNREADABLE" in store.index_markdown()       # page 3
    assert store.unreadable_pages() == [3]


def _fast_cfg(monkeypatch):
    monkeypatch.setattr(ingest, "render_page_png", lambda *a, **k: b"\x89PNG")
    cfg = Config()
    cfg.chat_provider = "mock"
    cfg.vision_provider = "mock"
    cfg.llm_max_retries = 3
    cfg.llm_backoff_base_seconds = 0.0
    return cfg


def test_injected_failure_recovers_on_retry(monkeypatch):
    cfg = _fast_cfg(monkeypatch)
    cfg.inject_read_failure_page = 1
    good = json.dumps({"text": "Serum creatinine 1.04", "doc_type": "lab_report",
                       "dates": [], "has_handwriting": False, "gist": "labs"})
    provider = MockProvider(cfg, transcriber=good)

    rec = _transcribe_one("x.pdf", 1, provider, cfg, tracer=None)
    assert rec.readable is True
    assert "creatinine" in rec.text
    assert provider.transcribe_calls == 1   # first attempt was the injected failure, 2nd called provider


def test_permanent_failure_marks_page_unreadable(monkeypatch):
    cfg = _fast_cfg(monkeypatch)

    def always_fail(*a, **k):
        from discharge_agent.llm.base import TransientLLMError
        raise TransientLLMError("vision down")

    provider = MockProvider(cfg, transcriber=always_fail)
    rec = _transcribe_one("x.pdf", 5, provider, cfg, tracer=None)
    assert rec.readable is False
    assert rec.doc_type == "unreadable"
    assert rec.error
