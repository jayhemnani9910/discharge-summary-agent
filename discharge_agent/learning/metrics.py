"""The reward / accuracy signal and the safety gate (Part 2 requirements #1 and #5).

The reward is derived purely from edits: how much a reviewer changes a draft section. Less
editing means a better draft. We measure it as a normalized edit distance at the section level
(``1 - difflib ratio``) so it is bounded in [0, 1] and needs no third-party dependency.

The safety gate is what stops the loop from gaming the reward. A draft can always lower its edit
distance by getting vaguer or by dropping the very flags that make it safe; the gate makes any
such candidate unselectable, so the reward can never be earned by degrading safety. It works on
text (candidates are section rewrites, not full draft states): every status marker and every
documented number in the reference must survive unchanged, and no new number may appear. That
catches both fabrication (a dose/date/value that was not in the source) and vagueness (a value
silently dropped).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Non-value statuses that must never be edited away: if the reference says a field is unknown or
# disputed, a "nicer" rewrite that hides that is unsafe.
_SAFETY_MARKERS = ("MISSING", "PENDING", "CONFLICT", "NOT DOCUMENTED", "NOT_DOCUMENTED")

# Numbers carry the clinical facts (doses, dates, ages, lab values). Reformatting may move them
# around but must neither invent nor drop one.
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# Month names map to their numeric form so a legitimate date restyle (26/02/2026 -> 26-Feb-2026)
# is not mistaken for dropping the month "02": the gate compares numbers after canonicalizing.
_MONTHS = {
    "january": "1", "jan": "1", "february": "2", "feb": "2", "march": "3", "mar": "3",
    "april": "4", "apr": "4", "may": "5", "june": "6", "jun": "6", "july": "7", "jul": "7",
    "august": "8", "aug": "8", "september": "9", "sep": "9", "sept": "9", "october": "10",
    "oct": "10", "november": "11", "nov": "11", "december": "12", "dec": "12",
}
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def edit_burden(draft_text: str, edited_text: str) -> float:
    """Normalized section-level edit distance in [0, 1]. 0 == identical, 1 == nothing in common.
    This is ``1 - reward``; the reviewer changing little means a low burden (a high reward)."""
    a, b = _norm(draft_text), _norm(edited_text)
    if not a and not b:
        return 0.0
    return 1.0 - SequenceMatcher(None, a, b).ratio()


def draft_burden(section_pairs: dict[str, tuple[str, str]]) -> float:
    """Mean edit burden across a draft's sections. ``section_pairs`` maps a section key to its
    (draft_text, edited_text). An empty draft has zero burden by definition."""
    if not section_pairs:
        return 0.0
    return sum(edit_burden(d, e) for d, e in section_pairs.values()) / len(section_pairs)


def _markers(text: str) -> set[str]:
    upper = (text or "").upper()
    return {m for m in _SAFETY_MARKERS if m in upper}


def _numbers(text: str) -> list[str]:
    # Canonicalize month names to their number first, and strip leading zeros so "02" == "2",
    # so date reformatting is value-preserving under this gate.
    canon = _MONTH_RE.sub(lambda m: " " + _MONTHS[m.group(0).lower()] + " ", text or "")
    return [str(int(n)) if n.isdigit() else n for n in _NUMBER_RE.findall(canon)]


def safety_retained(reference_text: str, candidate_text: str) -> bool:
    """True iff ``candidate_text`` is a safe restyling of ``reference_text``: every safety marker
    in the reference survives, and the multiset of documented numbers is unchanged (nothing
    invented, nothing dropped). A candidate that fails this is unselectable regardless of how low
    its edit distance is."""
    if not _markers(reference_text) <= _markers(candidate_text):
        return False
    return sorted(_numbers(reference_text)) == sorted(_numbers(candidate_text))
