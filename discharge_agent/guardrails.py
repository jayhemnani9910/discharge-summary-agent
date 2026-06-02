"""The no-fabrication guardrail (Hard requirement #3), enforced in code.

Two structural checks back up the prompt-level instruction:

1. ``quote_supported`` — a value may only be recorded if the verbatim ``quote`` the
   agent cites is actually present in that page's transcription. If the model tries to
   record a value whose quote is not on the page, the tool call is rejected and the
   agent is told to flag instead. This makes a citation mandatory and checkable.

2. ``finalize_check`` — the draft cannot be finalized while any required section is
   still unaddressed, or while the mandatory medication-reconciliation and
   drug-interaction steps have not run (when discharge meds exist).

A separate, stronger semantic check (an adversarial LLM re-read of every value against
its source) lives in verify.py and runs before the draft is written out.
"""

from __future__ import annotations

import re

from .schema import SECTION_BY_KEY
from .state import DraftState, FieldStatus


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def quote_supported(store, page: int, quote: str) -> tuple[bool, str]:
    """Is ``quote`` actually present on ``page``? Returns (ok, reason)."""
    rec = store.get(page)
    if rec is None:
        return False, f"page {page} is not in the record"
    if not rec.readable:
        return False, f"page {page} is unreadable; its content cannot be cited"
    q = _norm(quote)
    if len(q) < 4:
        return False, "quote is too short to verify; cite the exact words from the page"
    if q not in _norm(rec.text):
        return False, f"quote not found verbatim on page {page}"
    return True, "ok"


def finalize_check(state: DraftState, store) -> tuple[bool, list[str]]:
    """Structural readiness for finalize. Returns (ready, list_of_remaining_items)."""
    remaining: list[str] = []

    for key, f in state.fields.items():
        if f.status == FieldStatus.EMPTY:
            label = SECTION_BY_KEY[key].label
            remaining.append(f"section '{label}' has not been addressed yet "
                             f"(record a sourced value, or call note_unavailable)")

    discharge_meds = state.meds_for("discharge")
    if discharge_meds:
        if not state.reconciliation_attempted:
            remaining.append("medication reconciliation not done: call reconcile_medications "
                             "after recording admission and discharge medications")
        if not state.interaction_check_done:
            remaining.append("drug-interaction check not done: call drug_interaction_check "
                             "on the discharge medications")

    # Every unreadable page must be surfaced, never silently dropped.
    flagged_pages = {p for fl in state.flags for p in fl.source_pages}
    for page in store.unreadable_pages():
        if page not in flagged_pages:
            remaining.append(f"unreadable page {page} has not been flagged for review")

    return (len(remaining) == 0, remaining)
