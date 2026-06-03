"""The agent's tools: their schemas (shown to the model) and their implementations.

Each tool either reads from the page store or writes into the draft state. Two
properties are enforced here rather than trusted to the prompt:

* values and medications can only be recorded with a page + verbatim quote that
  actually appears on that page (``guardrails.quote_supported``);
* a medication change with no documented, quote-backed reason is flagged
  automatically, and any drug interaction found is flagged automatically.

``drug_interaction_check`` is a *mock* external tool (a small local table). The agent
decides when to call it; the assignment only requires that the decision is the agent's
and that any finding is surfaced, not buried.
"""

from __future__ import annotations

from .guardrails import quote_supported
from .llm.base import ToolSpec
from .schema import SECTION_BY_KEY, SECTION_KEYS
from .state import DraftState, FieldStatus, Severity

# --- mock clinical knowledge ------------------------------------------------
# Brand -> generic (lowercased). Only what is plausibly in this dataset; unknown
# brands are kept verbatim and simply won't match the interaction table.
_BRAND_TO_GENERIC = {
    "raciper": "rabeprazole", "pan": "pantoprazole", "pantop": "pantoprazole",
    "emeset": "ondansetron", "zofer": "ondansetron", "ondansetron": "ondansetron",
    "oflox": "ofloxacin", "oflox tz": "ofloxacin", "ofloxacin": "ofloxacin",
    "ciplox": "ciprofloxacin", "ciprofloxacin": "ciprofloxacin",
    "levoflox": "levofloxacin", "levofloxacin": "levofloxacin",
    "meftal": "mefenamic acid", "meftal spas": "mefenamic acid",
    "lopiramide": "loperamide", "loperamide": "loperamide",
    "insulin": "insulin", "actrapid": "insulin", "human actrapid": "insulin",
    "lantus": "insulin glargine", "metformin": "metformin",
    # OCR/brand variants of the same drug seen in this record's charts. Merging these
    # stops reconciliation reporting one drug twice (e.g. Meromac vs Meropdac for
    # meropenem); genuinely distinct names are left unmapped and still surface.
    "meromac": "meropenem", "meropdac": "meropenem", "meropenem": "meropenem",
    "pantodac": "pantoprazole", "somol": "sumol",
}

# Drugs that prolong the QT interval; two together is an additive risk worth flagging.
_QT_PROLONGING = {"ondansetron", "ofloxacin", "ciprofloxacin", "levofloxacin",
                  "loperamide", "domperidone"}

# A few explicit pairwise interactions beyond the additive-QT rule.
_PAIR_INTERACTIONS = {
    frozenset({"ofloxacin", "insulin"}):
        ("Fluoroquinolones can disturb glucose control; monitor blood sugar.", "medium"),
    frozenset({"ciprofloxacin", "insulin"}):
        ("Fluoroquinolones can disturb glucose control; monitor blood sugar.", "medium"),
    frozenset({"mefenamic acid", "insulin"}):
        ("NSAID with diabetes/dehydration: renal caution.", "medium"),
}


def _canon(text: str) -> str:
    """Collapse a name to comparable letters/digits only (drops spaces and punctuation)."""
    import re

    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _generic(name: str) -> str:
    """Normalise a medication name so OCR/format variants of one drug collapse together.

    Strips dosage-form words (INJ/TAB/T/CAP/SYP) and any parenthetical annotation, then
    matches the brand table against WHOLE words of the name (a contiguous run of tokens),
    not as a raw substring -- a substring match would wrongly fold "ciprofloxacin" into
    "ofloxacin" because "oflox" is a substring of it. Unknown names fall through to their
    own canonical key (so they are never silently merged into another drug; they just
    won't match the interaction table).
    """
    import re

    key = (name or "").strip().lower()
    key = re.sub(r"\([^)]*\)", " ", key)  # drop "(meropenem)" style annotations
    key = re.sub(r"\b(inj|tab|tablet|cap|capsule|syp|syrup|t)\b\.?", " ", key)
    tokens = [_canon(t) for t in re.split(r"\s+", key) if _canon(t)]
    # Longer (multi-word) brand keys first so "oflox tz" wins over "oflox".
    for brand, gen in sorted(_BRAND_TO_GENERIC.items(), key=lambda kv: -len(kv[0].split())):
        bwords = [_canon(t) for t in brand.split() if _canon(t)]
        if not bwords:
            continue
        n = len(bwords)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == bwords:
                return gen
    return _canon(key)


# --- tool schemas (uppercase JSON-schema types, as Gemini expects) ----------
TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        "read_page",
        "Return the full transcription of one source page so you can read it and copy exact quotes.",
        {"type": "OBJECT", "properties": {"page": {"type": "INTEGER"}}, "required": ["page"]},
    ),
    ToolSpec(
        "search_notes",
        "Keyword-search every transcribed page; returns matching pages with snippets.",
        {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]},
    ),
    ToolSpec(
        "record_field",
        "Record a sourced value for a discharge-summary section. You MUST supply the page "
        "number and a verbatim quote that appears on that page; otherwise the call is rejected. "
        "Recording two different values for a single-value section marks it as a CONFLICT.",
        {"type": "OBJECT", "properties": {
            "section": {"type": "STRING", "enum": list(SECTION_KEYS)},
            "value": {"type": "STRING", "description": "the value, normalised for the summary"},
            "source_page": {"type": "INTEGER"},
            "quote": {"type": "STRING", "description": "exact words from the page supporting the value"},
            "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
        }, "required": ["section", "value", "source_page", "quote"]},
    ),
    ToolSpec(
        "note_unavailable",
        "Mark a section as MISSING (absent from all notes), PENDING (result awaited), CONFLICT "
        "(notes disagree), or NOT_DOCUMENTED. Use this instead of guessing. It also raises a flag.",
        {"type": "OBJECT", "properties": {
            "section": {"type": "STRING", "enum": list(SECTION_KEYS)},
            "status": {"type": "STRING", "enum": ["MISSING", "PENDING", "CONFLICT", "NOT_DOCUMENTED"]},
            "detail": {"type": "STRING"},
            "source_pages": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        }, "required": ["section", "status", "detail"]},
    ),
    ToolSpec(
        "record_medication",
        "Record one medication for the admission list or the discharge list, with page + quote.",
        {"type": "OBJECT", "properties": {
            "stage": {"type": "STRING", "enum": ["admission", "discharge"]},
            "name": {"type": "STRING"},
            "details": {"type": "STRING", "description": "dose / frequency / route as written"},
            "source_page": {"type": "INTEGER"},
            "quote": {"type": "STRING"},
        }, "required": ["stage", "name", "details", "source_page", "quote"]},
    ),
    ToolSpec(
        "reconcile_medications",
        "Compare the recorded admission and discharge medication lists and produce the changes "
        "(added/stopped/changed/continued). Optionally pass documented reasons you found in the "
        "notes (each with page + quote). Any change without a quote-backed reason is flagged.",
        {"type": "OBJECT", "properties": {
            "documented_reasons": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "medication": {"type": "STRING"},
                "reason": {"type": "STRING"},
                "source_page": {"type": "INTEGER"},
                "quote": {"type": "STRING"},
            }, "required": ["medication", "reason", "source_page", "quote"]}},
        }},
    ),
    ToolSpec(
        "drug_interaction_check",
        "Check a list of medications against the local (mock, limited) interaction database. "
        "Call this once you have the discharge medications. Any interaction returned must be flagged.",
        {"type": "OBJECT", "properties": {
            "medications": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": ["medications"]},
    ),
    ToolSpec(
        "flag_for_clinician_review",
        "Raise a flag for the reviewing clinician: pending result, missing data, conflict, "
        "medication reconciliation, drug interaction, unreadable page, or any safety concern.",
        {"type": "OBJECT", "properties": {
            "field": {"type": "STRING"},
            "issue_type": {"type": "STRING", "enum": ["pending", "missing", "conflict",
                "med_reconciliation", "drug_interaction", "unreadable", "safety", "other"]},
            "detail": {"type": "STRING"},
            "severity": {"type": "STRING", "enum": ["info", "low", "medium", "high", "critical"]},
            "source_pages": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        }, "required": ["field", "issue_type", "detail", "severity"]},
    ),
    ToolSpec(
        "finalize_draft",
        "Attempt to finalize the draft. Succeeds only when every section has been addressed and "
        "the medication-reconciliation and interaction checks have run. Otherwise it tells you "
        "what is left.",
        {"type": "OBJECT", "properties": {}},
    ),
]

TOOL_NAMES = {t.name for t in TOOL_SPECS}


class ToolDispatcher:
    def __init__(self, store, state: DraftState, config, tracer=None):
        self.store = store
        self.state = state
        self.config = config
        self.tracer = tracer

    def dispatch(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            return handler(args or {})
        except KeyError as exc:
            return {"error": f"missing required argument: {exc}"}
        except Exception as exc:  # noqa: BLE001 - surface, never crash the loop
            return {"error": f"{type(exc).__name__}: {exc}"}

    # --- reading ------------------------------------------------------------
    def _t_read_page(self, args: dict) -> dict:
        page = int(args["page"])
        rec = self.store.get(page)
        if rec is None:
            return {"error": f"page {page} does not exist (record has {self.store.num_pages} pages)"}
        if not rec.readable:
            return {"page": page, "readable": False,
                    "note": "this page could not be transcribed; do not infer its contents",
                    "error": rec.error}
        # Bound the text returned into the conversation to keep context manageable.
        # Quote verification still checks against the full stored page text, so a quote
        # must come from what the agent actually saw here.
        text = rec.text
        truncated = len(text) > 3500
        out = {"page": page, "readable": True, "doc_type": rec.doc_type, "text": text[:3500]}
        if truncated:
            out["note"] = "text truncated to 3500 chars; call search_notes for specifics on this page"
        return out

    def _t_search_notes(self, args: dict) -> dict:
        hits = self.store.search(str(args["query"]))
        return {"query": args["query"], "hits": hits} if hits else {
            "query": args["query"], "hits": [], "note": "no pages matched this query"}

    # --- writing values -----------------------------------------------------
    def _t_record_field(self, args: dict) -> dict:
        section = args["section"]
        if section not in SECTION_BY_KEY:
            return {"error": f"unknown section {section!r}; valid: {list(SECTION_KEYS)}"}
        page = int(args["source_page"])
        ok, reason = quote_supported(self.store, page, args["quote"])
        if not ok:
            # Rejection is the guardrail doing its job: no citation, no value.
            return {"rejected": True, "reason": reason,
                    "advice": "Read the page and cite exact words, or use note_unavailable / flag."}
        f = self.state.record_value(section, args["value"], page, args["quote"],
                                    args.get("confidence", "medium"))
        if f.status == FieldStatus.CONFLICT and len(f.values) >= 2:
            pages = sorted({v.source_page for v in f.values})
            self._ensure_flag(section, "conflict",
                              f"Sources disagree on {SECTION_BY_KEY[section].label}: "
                              + "; ".join(f"'{v.value}' (p{v.source_page})" for v in f.values),
                              Severity.HIGH, pages)
            return {"ok": True, "status": "CONFLICT",
                    "note": "differing values kept and flagged; not resolved automatically"}
        return {"ok": True, "status": f.status.value}

    def _t_note_unavailable(self, args: dict) -> dict:
        section = args["section"]
        if section not in SECTION_BY_KEY:
            return {"error": f"unknown section {section!r}"}
        status = FieldStatus(args["status"])
        self.state.set_status(section, status, args.get("detail", ""))
        issue = {"PENDING": "pending", "MISSING": "missing", "CONFLICT": "conflict",
                 "NOT_DOCUMENTED": "missing"}[status.value]
        sev = Severity.HIGH if status in (FieldStatus.PENDING, FieldStatus.CONFLICT) else Severity.MEDIUM
        self._ensure_flag(section, issue,
                          f"{SECTION_BY_KEY[section].label}: {status.value} — {args.get('detail','')}",
                          sev, args.get("source_pages"))
        return {"ok": True, "status": status.value}

    def _t_record_medication(self, args: dict) -> dict:
        page = int(args["source_page"])
        ok, reason = quote_supported(self.store, page, args["quote"])
        if not ok:
            return {"rejected": True, "reason": reason}
        self.state.add_medication(args["stage"], args["name"], args.get("details", ""),
                                  page, args["quote"])
        # Recording a discharge medication marks the discharge_medications section as
        # addressed; the meds themselves render from the medication list, not from a
        # SourcedValue, but the section must no longer count as "unaddressed".
        if args["stage"] == "discharge":
            fld = self.state.fields["discharge_medications"]
            if fld.status == FieldStatus.EMPTY:
                fld.status = FieldStatus.VALUE
        return {"ok": True, "stage": args["stage"], "name": args["name"]}

    # --- reconciliation -----------------------------------------------------
    def _t_reconcile_medications(self, args: dict) -> dict:
        from .state import MedChange

        adm = self.state.meds_for("admission")
        dis = self.state.meds_for("discharge")
        self.state.reconciliation_attempted = True

        # Index documented reasons by generic name, keeping only quote-backed ones.
        reasons: dict[str, dict] = {}
        for r in args.get("documented_reasons", []) or []:
            ok, _ = quote_supported(self.store, int(r.get("source_page", -1)), r.get("quote", ""))
            if ok:
                reasons[_generic(r["medication"])] = {
                    "reason": r["reason"], "page": int(r["source_page"])}

        adm_by = {_generic(m.name): m for m in adm}
        dis_by = {_generic(m.name): m for m in dis}

        # Clear any prior reconciliation flags so repeated calls don't duplicate them.
        self.state.flags = [fl for fl in self.state.flags if fl.issue_type != "med_reconciliation"]
        self.state.med_changes = []

        changes = []
        for g, m in dis_by.items():
            if g not in adm_by:
                changes.append(self._mk_change(g, "added", "", m.details, m.source_page, reasons))
            elif _detail_norm(adm_by[g].details) != _detail_norm(m.details):
                changes.append(self._mk_change(g, "changed", adm_by[g].details, m.details,
                                               m.source_page, reasons, adm_by[g].source_page))
            else:
                changes.append(self._mk_change(g, "continued", adm_by[g].details, m.details,
                                               m.source_page, reasons))
        for g, m in adm_by.items():
            if g not in dis_by:
                changes.append(self._mk_change(g, "stopped", m.details, "", m.source_page, reasons))

        self.state.med_changes = changes
        flagged = []
        for c in changes:
            if c.change_type in ("added", "stopped", "changed") and not c.reason_documented:
                self._ensure_flag("discharge_medications", "med_reconciliation",
                                  f"{c.name} {c.change_type.upper()} with no documented reason",
                                  Severity.HIGH, c.source_pages)
                flagged.append(c.name)
        return {"ok": True,
                "changes": [c.to_dict() for c in changes],
                "flagged_no_reason": flagged,
                "note": "changes without a quote-backed reason were flagged for reconciliation"}

    def _mk_change(self, generic, ctype, adm_detail, dis_detail, page, reasons, adm_page=None):
        from .state import MedChange

        r = reasons.get(generic)
        pages = [p for p in [page, adm_page] if p]
        return MedChange(
            name=generic, change_type=ctype, admission_detail=adm_detail,
            discharge_detail=dis_detail,
            reason=(r["reason"] if r else ""), reason_documented=bool(r),
            source_pages=sorted(set(pages)),
        )

    # --- interaction check (mock external tool) -----------------------------
    def _t_drug_interaction_check(self, args: dict) -> dict:
        meds = [str(m) for m in args.get("medications", [])]
        generics = sorted({_generic(m) for m in meds})
        found = []
        qt = [g for g in generics if g in _QT_PROLONGING]
        if len(qt) >= 2:
            found.append({"drugs": qt, "severity": "high",
                          "description": "Additive QT-interval prolongation risk from combining "
                          + " + ".join(qt) + "."})
        for i in range(len(generics)):
            for j in range(i + 1, len(generics)):
                pair = frozenset({generics[i], generics[j]})
                if pair in _PAIR_INTERACTIONS:
                    desc, sev = _PAIR_INTERACTIONS[pair]
                    found.append({"drugs": sorted(pair), "severity": sev, "description": desc})

        self.state.interaction_check_done = True
        self.state.interaction_checks.append({"input": meds, "interactions": found})
        for it in found:
            self._ensure_flag("discharge_medications", "drug_interaction",
                              f"Interaction ({it['severity']}): {it['description']}",
                              Severity(it["severity"]), [])
        return {"ok": True, "interactions": found,
                "disclaimer": "Mock interaction database with limited coverage; "
                "absence of a result is NOT a guarantee of safety."}

    # --- flags & finalize ---------------------------------------------------
    def _t_flag_for_clinician_review(self, args: dict) -> dict:
        self.state.add_flag(args["field"], args["issue_type"], args["detail"],
                            Severity(args.get("severity", "medium")), args.get("source_pages"))
        return {"ok": True}

    def _t_finalize_draft(self, args: dict) -> dict:
        from .guardrails import finalize_check

        ready, remaining = finalize_check(self.state, self.store)
        return {"ready": ready, "remaining": remaining}

    # --- helper -------------------------------------------------------------
    def _ensure_flag(self, field_name, issue_type, detail, severity, source_pages):
        """Add a flag unless an identical one already exists (idempotent)."""
        for fl in self.state.flags:
            if fl.field == field_name and fl.issue_type == issue_type and fl.detail == detail:
                return
        self.state.add_flag(field_name, issue_type, detail, severity, source_pages)


def _detail_norm(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", (text or "").strip().lower())
