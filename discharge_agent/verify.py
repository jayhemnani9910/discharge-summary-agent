"""Post-hoc fact verification (second layer of the no-fabrication guardrail).

After the agent finalizes, every recorded value is independently re-checked against its
cited page by a separate, deliberately strict LLM pass. A value the source does not
*directly* support is removed from the draft and replaced by a flag. This catches the
failure mode where a quote technically appears on the page but does not actually support
the value the agent wrote (mis-citation, wrong row of a table, stale value, etc.).

If verification itself fails (e.g. the API is down) the value is NOT quietly accepted:
it is left marked unverified and a flag is raised, honouring "never treat a failed call
as success".
"""

from __future__ import annotations

import json

from .retry import call_with_retries
from .schema import SECTION_BY_KEY
from .state import DraftState, FieldStatus, Severity

_VERIFIER_SYSTEM = (
    "You are a strict clinical documentation fact-checker. You decide only whether each "
    "claimed VALUE is directly supported by the SOURCE TEXT quoted from a single page. "
    "Be conservative: if the source does not clearly state the value, mark it unsupported. "
    "Never use outside knowledge. Reply with JSON only."
)


def verify_draft(provider, store, state: DraftState, config, tracer=None) -> dict:
    checked = 0
    downgraded = 0
    for key, f in state.fields.items():
        if f.status not in (FieldStatus.VALUE, FieldStatus.CONFLICT) or not f.values:
            continue

        # A narrative section (hospital course) is a synthesis across pages, not a single
        # quotable fact. We do not drop it; we keep it but mark it unverified and flag it
        # for the clinician to read against the record. (Discrete facts elsewhere are still
        # strictly verified below.)
        if SECTION_BY_KEY[key].narrative:
            for v in f.values:
                v.verified = False
                v.verifier_note = "synthesized summary; not individually quote-verified"
            state.add_flag(key, "safety",
                           f"'{key}' is a synthesized narrative; verify it against the source "
                           "record before use.", Severity.MEDIUM,
                           sorted({v.source_page for v in f.values}))
            continue

        verdicts = _verify_section(provider, store, key, f, config, tracer)
        if verdicts is None:
            # Verification could not run; keep values but mark unverified + flag.
            for v in f.values:
                v.verified = False
            state.add_flag(key, "safety",
                           f"Automated verification could not run for '{key}'; values are "
                           "unverified and must be checked manually.", Severity.HIGH, [])
            continue

        kept = []
        for i, v in enumerate(f.values):
            checked += 1
            verdict = verdicts.get(i, {"verdict": "unsupported", "note": "no verdict returned"})
            if verdict.get("verdict") == "supported":
                v.verified = True
                v.verifier_note = verdict.get("note", "")
                kept.append(v)
            else:
                downgraded += 1
                state.add_flag(key, "safety",
                               f"Removed unverified value '{v.value}' (page {v.source_page}): "
                               f"{verdict.get('note','not supported by source')}",
                               Severity.HIGH, [v.source_page])
                if tracer:
                    tracer.emit("verify", message=f"{key}: dropped unsupported value "
                                f"'{v.value}' (p{v.source_page})")
        f.values = kept
        if not f.values:
            f.status = FieldStatus.MISSING
            f.detail = "value(s) removed by verifier as unsupported"
        elif f.status == FieldStatus.CONFLICT and len(f.values) < 2:
            f.status = FieldStatus.VALUE

    summary = {"values_checked": checked, "values_downgraded": downgraded}
    if tracer:
        tracer.emit("verify", message=f"Verification done: {summary}")
    return summary


def _verify_section(provider, store, key, field, config, tracer):
    claims = []
    for i, v in enumerate(field.values):
        rec = store.get(v.source_page)
        page_text = (rec.text if rec and rec.readable else "")[:4000]
        claims.append({"index": i, "section": key, "value": v.value,
                       "source_page": v.source_page, "source_text": page_text})

    prompt = (
        "For each claim, decide if VALUE is directly supported by SOURCE TEXT (from that "
        "page only). Reply JSON: {\"verdicts\":[{\"index\":int,\"verdict\":"
        "\"supported\"|\"unsupported\"|\"partial\",\"note\":str}]}.\n\nCLAIMS:\n"
        + json.dumps(claims, ensure_ascii=False)
    )
    try:
        raw = call_with_retries(
            lambda: provider.complete(_VERIFIER_SYSTEM, prompt, json_mode=True),
            config=config, tracer=tracer, what=f"verify {key}")
    except Exception as exc:  # noqa: BLE001
        if tracer:
            tracer.emit("verify", message=f"verifier failed for {key}: {exc}")
        return None

    data = _parse(raw)
    if data is None:
        return None
    out = {}
    for item in data.get("verdicts", []):
        try:
            out[int(item["index"])] = {"verdict": item.get("verdict", "unsupported"),
                                       "note": item.get("note", "")}
        except (KeyError, ValueError, TypeError):
            continue
    return out


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
