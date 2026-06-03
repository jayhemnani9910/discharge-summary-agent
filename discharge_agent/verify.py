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
import re

from .guardrails import quote_supported
from .retry import call_with_retries
from .schema import SECTION_BY_KEY
from .state import DraftState, FieldStatus, Severity


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())

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

        # A narrative section (hospital course) is a synthesis across pages. It is flagged as a
        # synthesis to read against the record, but each clause is STILL verified against its own
        # cited page below (no blanket bypass): an unsupported clause is dropped like any value.
        if SECTION_BY_KEY[key].narrative:
            state.add_flag(key, "safety",
                           f"'{key}' is a synthesized narrative; verify it against the source "
                           "record before use.", Severity.MEDIUM,
                           sorted({v.source_page for v in f.values}))

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

    med_checked, med_flagged = _verify_medications(provider, store, state, config, tracer)
    summary = {"values_checked": checked, "values_downgraded": downgraded,
               "medications_checked": med_checked, "medications_flagged": med_flagged}
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


def _verify_medications(provider, store, state: DraftState, config, tracer=None):
    """Re-check each recorded medication (name + dose) against its cited page, mirroring the
    field verifier. An unsupported medication is marked unverified and flagged (it is not
    dropped, so the already-computed reconciliation table stays consistent). On verifier failure,
    every medication is marked unverified and flagged. Returns (checked, flagged)."""
    meds = list(state.medications)
    if not meds:
        return 0, 0
    claims = []
    for i, m in enumerate(meds):
        rec = store.get(m.source_page)
        page_text = (rec.text if rec and rec.readable else "")[:4000]
        claims.append({"index": i, "section": f"{m.stage}_medication",
                       "value": f"{m.name} {m.details}".strip(),
                       "source_page": m.source_page, "source_text": page_text})
    prompt = (
        "For each claim, decide if VALUE (a medication name and dose) is directly supported by "
        "SOURCE TEXT (from that page only). Reply JSON: {\"verdicts\":[{\"index\":int,\"verdict\":"
        "\"supported\"|\"unsupported\"|\"partial\",\"note\":str}]}.\n\nCLAIMS:\n"
        + json.dumps(claims, ensure_ascii=False))
    try:
        raw = call_with_retries(
            lambda: provider.complete(_VERIFIER_SYSTEM, prompt, json_mode=True),
            config=config, tracer=tracer, what="verify medications")
    except Exception as exc:  # noqa: BLE001 - never treat a failed verification as success
        for m in meds:
            m.verified = False
        state.add_flag("discharge_medications", "safety",
                       "Automated verification could not run for medications; review them "
                       "manually.", Severity.HIGH, [])
        if tracer:
            tracer.emit("verify", message=f"medication verifier failed: {exc}")
        return 0, 0
    data = _parse(raw) or {}
    verdicts = {}
    for item in data.get("verdicts", []):
        try:
            verdicts[int(item["index"])] = item.get("verdict", "unsupported")
        except (KeyError, ValueError, TypeError):
            continue
    checked = flagged = 0
    for i, m in enumerate(meds):
        checked += 1
        if verdicts.get(i, "unsupported") == "supported":
            m.verified = True
        else:
            m.verified = False
            flagged += 1
            label = f"{m.name} {m.details}".strip()
            state.add_flag(f"{m.stage}_medications", "safety",
                           f"{m.stage.capitalize()} medication '{label}' (page {m.source_page}) "
                           "may not be supported by its cited page; verify before use.",
                           Severity.HIGH, [m.source_page])
            if tracer:
                tracer.emit("verify", message=f"medication unverified: '{label}' (p{m.source_page})")
    return checked, flagged


_DETECT_SYSTEM = (
    "You are a clinical record auditor. You ONLY identify when the SAME single-valued field is "
    "documented with a DIFFERENT value on another page (a conflict the draft must flag). Never "
    "invent values; quote verbatim from the page. Reply with JSON only."
)

_SINGLE_FIELDS_TO_SCAN = (
    "principal_diagnosis", "admission_date", "discharge_date", "allergies", "discharge_condition",
)


def detect_conflicts(provider, store, state: DraftState, config, tracer=None) -> int:
    """Guaranteed, code-driven conflict scan run AFTER the loop (so it does not rely on the
    agent's prompt). For each single-valued field holding ONE recorded value, ask whether any
    page documents a DIFFERENT value: a verbatim-supported alternative is recorded and upgrades
    the field to CONFLICT; an unverifiable one raises a softer review flag instead of fabricating
    a value. Best-effort -- it never fails the draft. Returns the number of conflict signals added."""
    targets = {k: state.fields[k] for k in _SINGLE_FIELDS_TO_SCAN
               if state.fields.get(k)
               and state.fields[k].status in (FieldStatus.VALUE, FieldStatus.MISSING)}
    if not targets:
        return 0
    recorded = {k: (f.values[0].value if f.values else None) for k, f in targets.items()}
    pages = [{"page": r.page, "text": (r.text or "")[:1500]} for r in store.records if r.readable]
    prompt = (
        "Recorded single-valued fields (null means the draft has no value yet for that field):\n"
        + json.dumps(recorded, ensure_ascii=False)
        + "\n\nFor EACH field, look across the SOURCE PAGES for documented value(s): for a field "
        "that already has a value, report only a DIFFERENT one; for a null field, report the "
        "documented value(s). Each value must be ATOMIC (a single diagnosis/date/etc., not a "
        "bundle). Reply JSON: {\"conflicts\":[{\"field\":str,\"value\":str,\"page\":int,\"quote\":"
        "str}]} where quote is verbatim from that page. If there is nothing to add reply "
        "{\"conflicts\":[]}.\n\nSOURCE PAGES:\n" + json.dumps(pages, ensure_ascii=False))
    try:
        raw = call_with_retries(
            lambda: provider.complete(_DETECT_SYSTEM, prompt, json_mode=True),
            config=config, tracer=tracer, what="conflict-scan")
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        if tracer:
            tracer.emit("verify", message=f"conflict-scan could not run: {exc}")
        return 0
    data = _parse(raw) or {}
    added = 0
    for c in data.get("conflicts", []):
        try:
            field = str(c["field"]); val = str(c["value"])
            page = int(c["page"]); quote = str(c["quote"])
        except (KeyError, ValueError, TypeError):
            continue
        if field not in targets:
            continue
        cur = recorded.get(field)
        if cur is not None and _norm(val) == _norm(cur):
            continue
        ok, _ = quote_supported(store, page, quote)
        if not ok:
            state.add_flag(field, "conflict",
                           f"Conflict scan: a different {SECTION_BY_KEY[field].label} may be "
                           f"documented ('{val}'); could not verify a quote, verify manually.",
                           Severity.MEDIUM, [page] if page else [])
            added += 1
            continue
        f = state.record_value(field, val, page, quote, "low")
        if f.status == FieldStatus.CONFLICT:
            state.add_flag(field, "conflict",
                           f"Conflict scan: {SECTION_BY_KEY[field].label} is also documented as "
                           f"'{val}' (p{page}); recorded value was '{cur or 'none'}'. Resolve "
                           "before use.", Severity.HIGH, [page])
            added += 1
            if tracer:
                tracer.emit("verify", message=f"conflict-scan upgraded {field} to CONFLICT")
        elif cur is None:
            state.add_flag(field, "conflict",
                           f"Conflict scan: recovered a {SECTION_BY_KEY[field].label} value "
                           f"'{val}' (p{page}) for a field the draft had left empty; verify.",
                           Severity.MEDIUM, [page])
            added += 1
    return added
