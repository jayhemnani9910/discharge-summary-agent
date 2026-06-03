"""Render the draft state into human-readable artifacts.

Outputs, per patient:
* draft.md   — the discharge summary draft for clinician review;
* draft.json — the same content, machine-readable (state.to_dict());
* flags.md   — every flag the agent raised, grouped by severity.

The renderer never adds content; it only formats what the agent recorded. Sections
without a verified value show their explicit status (MISSING/PENDING/CONFLICT), so the
reader can see exactly what is known and what is not.
"""

from __future__ import annotations

import json

from .schema import SECTIONS, SECTION_BY_KEY
from .state import DraftState, FieldStatus

_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
_STATUS_BANNER = {
    FieldStatus.MISSING: "MISSING — not found in any source document",
    FieldStatus.PENDING: "PENDING — result awaited at time of discharge",
    FieldStatus.CONFLICT: "CONFLICT — sources disagree (both values shown below)",
    FieldStatus.NOT_DOCUMENTED: "NOT DOCUMENTED in the source notes",
    FieldStatus.EMPTY: "NOT ADDRESSED",
}


def render_all(result, out_dir: str) -> dict[str, str]:
    import os

    os.makedirs(out_dir, exist_ok=True)
    files = {
        "draft.md": render_draft_md(result),
        "draft.json": json.dumps(result.state.to_dict(), ensure_ascii=False, indent=2),
        "flags.md": render_flags_md(result.state),
        "trace.md": result.tracer.to_markdown(),
    }
    for name, content in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    return files


def render_draft_md(result) -> str:
    state: DraftState = result.state
    status = ("COMPLETE draft — all sections addressed" if result.finalized
              else f"PARTIAL / INCOMPLETE — {state.incomplete_reason or result.stop_reason}")
    n_flags = len(state.flags)
    lines = [
        "# Discharge Summary — DRAFT FOR CLINICIAN REVIEW",
        "",
        "**This is an automated draft, not a final clinical document.** Every value is cited to a "
        "source page. Fields shown as MISSING / PENDING / CONFLICT were deliberately not filled. "
        f"There are {n_flags} flag(s) requiring clinician attention (see flags.md / the Flags "
        "section).",
        "",
        f"- **Patient:** {state.patient_id}",
        f"- **Draft status:** {status}",
        f"- **Agent steps:** {result.steps}  |  **Stop reason:** {result.stop_reason}",
        "",
        "---",
        "",
    ]

    for s in SECTIONS:
        if s.key == "discharge_medications":
            lines += _render_medications(state)
            continue
        lines += _render_section(state, s.key)

    lines += ["", "---", "", "## Flags for clinician review", ""]
    lines += _flag_lines(state) or ["_None raised._"]
    return "\n".join(lines) + "\n"


def _render_section(state: DraftState, key: str) -> list[str]:
    s = SECTION_BY_KEY[key]
    f = state.fields[key]
    out = [f"## {s.label}"]
    # PENDING sections (e.g. pending_results) still carry their cited items; we show the
    # banner *and* the items so the reader sees what was sent and that it is awaited.
    if f.values and f.status in (FieldStatus.VALUE, FieldStatus.CONFLICT, FieldStatus.PENDING):
        if f.status in (FieldStatus.CONFLICT, FieldStatus.PENDING):
            out.append(f"**{_STATUS_BANNER[f.status]}**")
        for v in f.values:
            mark = "" if v.verified else " _(unverified)_"
            out.append(f"- {v.value}  \n  _[source: page {v.source_page}; "
                       f"confidence {v.confidence}{mark}]_")
    else:
        banner = _STATUS_BANNER.get(f.status, f.status.value)
        out.append(f"**{banner}**")
        if f.detail:
            out.append(f"_{f.detail}_")
    out.append("")
    return out


def _render_medications(state: DraftState) -> list[str]:
    f = state.fields["discharge_medications"]
    out = ["## Discharge Medications"]

    discharge = state.meds_for("discharge")
    admission = state.meds_for("admission")
    if discharge:
        for m in discharge:
            out.append(f"- {m.name} — {m.details}  \n  _[source: page {m.source_page}]_")
    elif f.status not in (FieldStatus.VALUE, FieldStatus.CONFLICT):
        out.append(f"**{_STATUS_BANNER.get(f.status, f.status.value)}**")
        if f.detail:
            out.append(f"_{f.detail}_")

    # Reconciliation table (changes from admission) — the requested explicit comparison.
    out += ["", "### Changes from admission (medication reconciliation)"]
    if not state.med_changes:
        out.append("_Reconciliation not performed or no admission medications recorded._")
    else:
        out.append("| Medication | Change | Admission | Discharge | Reason |")
        out.append("|---|---|---|---|---|")
        for c in state.med_changes:
            if c.change_type == "continued":
                reason = c.reason if c.reason_documented else "(unchanged)"
            elif c.reason_documented:
                reason = c.reason
            else:
                reason = "**NO DOCUMENTED REASON — flagged**"
            out.append(f"| {c.name} | {c.change_type} | {c.admission_detail or '-'} "
                       f"| {c.discharge_detail or '-'} | {reason} |")
    if admission:
        out += ["", "_Admission medications on record:_ "
                + "; ".join(f"{m.name} ({m.details})" for m in admission)]
    if state.interaction_checks:
        any_found = [it for chk in state.interaction_checks for it in chk["interactions"]]
        out += ["", f"_Drug-interaction check run; {len(any_found)} interaction(s) found "
                "(see flags). Screen used a limited mock database; absence of a result is "
                "NOT a guarantee of safety._"]
    out.append("")
    return out


def render_flags_md(state: DraftState) -> str:
    lines = ["# Flags for clinician review", "",
             f"Total: {len(state.flags)}", ""]
    body = _flag_lines(state)
    lines += body or ["_None raised._"]
    return "\n".join(lines) + "\n"


def _flag_lines(state: DraftState) -> list[str]:
    by_sev = {s: [] for s in _SEVERITY_ORDER}
    for fl in state.flags:
        by_sev.setdefault(fl.severity.value, []).append(fl)
    out = []
    for sev in _SEVERITY_ORDER:
        items = by_sev.get(sev) or []
        if not items:
            continue
        out.append(f"### {sev.upper()}")
        for fl in items:
            pages = f" (pages {fl.source_pages})" if fl.source_pages else ""
            out.append(f"- **[{fl.issue_type}] {fl.field}**: {fl.detail}{pages}")
        out.append("")
    return out
