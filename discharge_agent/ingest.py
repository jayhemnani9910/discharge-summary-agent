"""PDF ingestion (Hard requirement #2).

The source documents are *scanned images* (typed printouts and handwriting), so there
is no text layer to extract. Ingestion therefore rasterises each page and asks the
vision model to transcribe it verbatim. This is a deterministic, cached pre-pass: the
agentic reasoning happens afterwards in agent.py, over this transcript.

Design notes:
* Transcriptions are cached to disk per page, so a re-run costs no API calls.
* The transcription prompt forbids guessing: unreadable handwriting must be marked
  ``[illegible]``. That uncertainty propagates so the agent flags rather than invents.
* A page that cannot be transcribed (after retries) is recorded as *unreadable* with an
  empty body, never as a blank-but-fine page. The agent must then flag it.
"""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import asdict, dataclass, field

import pypdfium2 as pdfium

from .llm.base import LLMProvider, TransientLLMError
from .retry import call_with_retries

_CACHE_VERSION = "v1"

_TRANSCRIBE_PROMPT = (
    "You are transcribing ONE page of a SYNTHETIC scanned clinical record. "
    "Return ONLY a JSON object with these keys:\n"
    '  "text": verbatim transcription of every word on the page, including all '
    "handwriting, table cells, headers and stamps. Preserve rows/labels as plain text. "
    "For anything you cannot read, write [illegible]; for an uncertain word write "
    "[guess?]. Do NOT guess clinical values.\n"
    '  "doc_type": one short label, e.g. discharge_summary, admission_note, '
    "progress_note, lab_report, medication_chart, vitals_chart, er_observation, "
    "nursing_note, consultation, checklist, consent, other.\n"
    '  "dates": array of every date visible, copied verbatim.\n'
    '  "has_handwriting": true/false.\n'
    '  "gist": one short sentence (max 20 words) summarising the page.\n'
    "Return the JSON only, no markdown fences."
)


@dataclass
class PageRecord:
    page: int                 # 1-based
    readable: bool
    text: str
    doc_type: str = "unknown"
    dates: list[str] = field(default_factory=list)
    has_handwriting: bool = False
    gist: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PageStore:
    """Holds every page's transcription and offers lookup/search for the tools."""

    def __init__(self, records: list[PageRecord]):
        self.records = sorted(records, key=lambda r: r.page)
        self._by_page = {r.page: r for r in self.records}

    @property
    def num_pages(self) -> int:
        return len(self.records)

    def get(self, page: int) -> PageRecord | None:
        return self._by_page.get(page)

    def unreadable_pages(self) -> list[int]:
        return [r.page for r in self.records if not r.readable]

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Cheap keyword search: score pages by how many query terms they contain,
        return a snippet around the first hit. Good enough to help the agent navigate."""
        terms = [t for t in re.findall(r"[a-zA-Z0-9]+", query.lower()) if len(t) > 1]
        hits = []
        for r in self.records:
            if not r.readable:
                continue
            low = r.text.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
                start = max(0, pos - 80)
                snippet = r.text[start:pos + 160].replace("\n", " ").strip()
                hits.append({"page": r.page, "doc_type": r.doc_type, "score": score,
                             "snippet": snippet})
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:limit]

    def index_markdown(self) -> str:
        """A compact per-page index the agent reads first to decide what to open."""
        lines = []
        for r in self.records:
            tag = "" if r.readable else " [UNREADABLE]"
            hw = " (handwritten)" if r.has_handwriting else ""
            dates = f" dates={','.join(r.dates)}" if r.dates else ""
            lines.append(f"- Page {r.page} [{r.doc_type}{hw}]{dates}{tag}: {r.gist}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"num_pages": self.num_pages, "pages": [r.to_dict() for r in self.records]}


# --- rendering --------------------------------------------------------------
def render_page_png(pdf_path: str, page_index0: int, long_edge_px: int) -> bytes:
    """Rasterise one page (0-based index) to PNG bytes at a sensible resolution."""
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[page_index0]
        width_pt, height_pt = page.get_size()
        scale = max(0.5, long_edge_px / max(width_pt, height_pt))
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()


def num_pages(pdf_path: str) -> int:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return len(pdf)
    finally:
        pdf.close()


# --- transcription ----------------------------------------------------------
def _parse_transcription(raw: str) -> dict:
    """Tolerantly pull the JSON object out of the model's reply."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Fall back to the first {...} block.
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    # Last resort: treat the whole reply as the transcription text.
    return {"text": raw, "doc_type": "unknown", "dates": [], "has_handwriting": False, "gist": ""}


def _cache_path(cache_dir: str, pdf_path: str, page: int) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", os.path.splitext(os.path.basename(pdf_path))[0])
    return os.path.join(cache_dir, f"{stem}_{_CACHE_VERSION}_page_{page:03d}.json")


def load_transcript_store(path: str) -> PageStore:
    """Build a PageStore from a pre-extracted transcript JSON.

    Used when no live vision provider is available (e.g. DeepSeek-only): the vision OCR
    was done once, and the agent reasons over its output. The expected shape is
    ``{"pages": [{page, full_transcription, doc_type, dates, is_handwritten, ...}], ...}``.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if "pages" not in data and isinstance(data.get("result"), dict):
        data = data["result"]
    records = []
    for p in data["pages"]:
        ent = p.get("entities", {}) or {}
        gist = "; ".join(
            x for x in [", ".join((ent.get("diagnoses") or [])[:1]),
                        ", ".join((ent.get("labs") or [])[:1])] if x
        ) or (p.get("gist") or (p.get("full_transcription", "")[:80]))
        records.append(PageRecord(
            page=int(p["page"]),
            readable=True,
            text=(p.get("full_transcription") or p.get("text") or "").strip(),
            doc_type=p.get("doc_type", "unknown") or "unknown",
            dates=list(p.get("dates", []) or []),
            has_handwriting=bool(p.get("is_handwritten", p.get("has_handwriting", False))),
            gist=gist.strip(),
        ))
    return PageStore(records)


def transcribe_pdf(
    pdf_path: str,
    provider: LLMProvider,
    config,
    tracer=None,
    cache_dir: str = "cache",
) -> PageStore:
    """Transcribe every page (using the cache where available) into a PageStore."""
    os.makedirs(cache_dir, exist_ok=True)
    total = num_pages(pdf_path)
    if tracer:
        tracer.emit("ingest", message=f"Ingesting {total} page(s) from {os.path.basename(pdf_path)}")

    records: list[PageRecord] = []
    for page in range(1, total + 1):
        cache_file = _cache_path(cache_dir, pdf_path, page)
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8") as fh:
                records.append(PageRecord(**json.load(fh)))
            continue

        rec = _transcribe_one(pdf_path, page, provider, config, tracer)
        with open(cache_file, "w", encoding="utf-8") as fh:
            json.dump(rec.to_dict(), fh, ensure_ascii=False, indent=2)
        records.append(rec)

    store = PageStore(records)
    if tracer:
        unreadable = store.unreadable_pages()
        msg = f"Ingestion complete: {store.num_pages} pages"
        if unreadable:
            msg += f"; UNREADABLE pages flagged for review: {unreadable}"
        tracer.emit("ingest", message=msg)
    return store


def _transcribe_one(pdf_path, page, provider, config, tracer) -> PageRecord:
    png = render_page_png(pdf_path, page - 1, config.render_long_edge_px)

    # Optional demonstration: force the first attempt on a chosen page to fail, so the
    # trace shows retry/backoff recovering (or, if it never recovers, the page flagged).
    inject = (config.inject_read_failure_page == page)
    state = {"failed_once": False}

    def attempt() -> str:
        if inject and not state["failed_once"]:
            state["failed_once"] = True
            raise TransientLLMError(f"injected transient read failure on page {page}")
        return provider.transcribe(png, "image/png", _TRANSCRIBE_PROMPT)

    try:
        raw = call_with_retries(attempt, config=config, tracer=tracer, what=f"transcribe p{page}")
    except Exception as exc:  # noqa: BLE001 - any failure becomes an explicit unreadable page
        if tracer:
            tracer.emit("ingest", message=f"Page {page} could not be transcribed: {exc}")
        return PageRecord(page=page, readable=False, text="", doc_type="unreadable",
                          error=str(exc))

    parsed = _parse_transcription(raw)
    return PageRecord(
        page=page,
        readable=True,
        text=str(parsed.get("text", "")).strip(),
        doc_type=str(parsed.get("doc_type", "unknown")) or "unknown",
        dates=list(parsed.get("dates", []) or []),
        has_handwriting=bool(parsed.get("has_handwriting", False)),
        gist=str(parsed.get("gist", "")).strip(),
    )
