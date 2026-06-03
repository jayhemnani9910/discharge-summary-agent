"""Candidate generation for best-of-N (Part 2 learning mechanism, the live step).

For each section the live LLM proposes several presentation-variants of the draft text. It is
told to keep every fact, citation, and flag and to vary only wording and format; the safety gate
in ``select`` independently enforces that, so we never rely on the prompt alone. The agent's own
draft is always candidate 0, so best-of-N can keep it and can never do worse than the baseline.

Every result is persisted under ``outputs/learning/candidates/`` keyed by a hash of the draft
text, so a later run replays the same candidates without re-spending API and the before/after
curve is reproducible. Generation failures degrade to "just the original draft", never a crash.
"""

from __future__ import annotations

import hashlib
import json
import os

from ..retry import call_with_retries

_CANDIDATE_SYSTEM = (
    "You restyle one section of a clinical discharge summary draft. Produce alternative "
    "phrasings that a clinician might prefer. Absolute rules: keep every fact, number, date, "
    "drug, dose, citation (page references) and status word (MISSING/PENDING/CONFLICT/NOT "
    "DOCUMENTED) exactly. Never add a fact that is not already present, never drop one, never "
    "remove a flag. Change only wording, order, and formatting. Reply with JSON only."
)


def _parse(raw: str):
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _key(section_key: str, draft_text: str) -> str:
    h = hashlib.sha1(draft_text.encode("utf-8")).hexdigest()[:12]
    return f"{section_key}__{h}"


def _cache_path(cache_dir: str, section_key: str, draft_text: str) -> str:
    return os.path.join(cache_dir, "candidates", _key(section_key, draft_text) + ".json")


def _generate(provider, section_key: str, draft_text: str, n: int, config, tracer):
    prompt = (
        f"SECTION: {section_key}\nN: {n - 1}\n\nProduce up to N alternative phrasings of the "
        "DRAFT TEXT below, each obeying every rule. Reply JSON: {\"variants\":[str, ...]}.\n\n"
        f"DRAFT TEXT:\n{draft_text}"
    )
    raw = call_with_retries(
        lambda: provider.complete(_CANDIDATE_SYSTEM, prompt, json_mode=True),
        config=config, tracer=tracer, what=f"candidates {section_key}")
    data = _parse(raw) or {}
    variants = [str(v).strip() for v in data.get("variants", []) if str(v).strip()]
    return variants[: n - 1]


def candidates_for(provider, section_key: str, draft_text: str, n: int, config,
                   cache_dir: str = "outputs/learning", regenerate: bool = False,
                   tracer=None) -> list[str]:
    """Return n candidate texts for a section, the original first. Reads the persisted cache
    unless ``regenerate`` is set. On any generation failure, returns just the original draft."""
    path = _cache_path(cache_dir, section_key, draft_text)
    if not regenerate and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("candidates"):
                return cached["candidates"]
        except (OSError, json.JSONDecodeError):
            pass

    candidates = [draft_text]
    try:
        candidates += _generate(provider, section_key, draft_text, n, config, tracer)
    except Exception as exc:  # noqa: BLE001 - a failed generation is reported, never fatal
        if tracer:
            tracer.emit("learn", message=f"candidate generation failed for {section_key}: {exc}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"section": section_key, "draft": draft_text, "candidates": candidates}, fh,
                  ensure_ascii=False, indent=2)
    return candidates
