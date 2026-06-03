"""Where the edit signal comes from (Part 2 requirement #2).

In production the edit signal is a clinician editing the draft. We do not have real edited data,
so the loop is exercised with a deterministic ``SimulatedReviewer`` that applies a consistent,
hidden house style. The key design choice is that both sources sit behind one interface,
``EditSource``: the reward model and the loop never know which is plugged in. In production you
construct ``DoctorEdits`` from the clinician's real corrections and change nothing else.

The simulated house style edits *presentation only*: it never chooses a diagnosis, fills a
missing value, or removes a flag. That is deliberate. The learner is rewarded for producing
drafts a reviewer edits less; if the reviewer changed clinical content, the learner would be
rewarded for changing medicine. Keeping the policy purely stylistic is what lets Part 2 reduce
edit burden without touching the Part 1 safety guarantee.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

_ABBREVIATIONS = {
    r"\bTab\b": "Tablet",
    r"\bCap\b": "Capsule",
    r"\bInj\b": "Injection",
    r"\bBD\b": "twice daily",
    r"\bOD\b": "once daily",
    r"\bTDS\b": "three times daily",
    r"\bQID\b": "four times daily",
    r"\bSOS\b": "as needed",
    r"\bIV\b": "intravenous",
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY or DD MM YYYY -> DD-Mon-YYYY.
_DATE_RE = re.compile(r"\b(\d{1,2})[\s/.\-](\d{1,2})[\s/.\-](\d{4})\b")
# A number stuck to a unit ("40mg") gets a space ("40 mg").
_UNIT_RE = re.compile(r"(\d)\s*(mg|mcg|ml|g|u|units?)\b", re.I)
# House citation style: "_[source: page 7; confidence high]_" -> "[ref: page 7]". The page
# number is kept (the only fact here); the confidence wording is house-dropped.
_CITE_RE = re.compile(r"_?\[source: page (\d+)[^\]]*\]_?", re.I)


class EditSource(ABC):
    """Returns the edited form of a draft section's text. The only thing the loop needs."""

    @abstractmethod
    def edit(self, section_key: str, draft_text: str) -> str:
        ...


class SimulatedReviewer(EditSource):
    """A deterministic stand-in clinician applying a fixed, hidden, style-only house policy."""

    def edit(self, section_key: str, draft_text: str) -> str:
        text = draft_text or ""
        for pattern, full in _ABBREVIATIONS.items():
            text = re.sub(pattern, full, text)
        text = _DATE_RE.sub(self._fmt_date, text)
        text = _UNIT_RE.sub(lambda m: f"{m.group(1)} {m.group(2).lower()}", text)
        text = _CITE_RE.sub(r"[ref: page \1]", text)
        text = text.replace("—", " - ")                 # em-dash -> spaced hyphen
        # House list style uses a bullet dot; collapse incidental whitespace (never across lines).
        lines = [re.sub(r"^\s*[-\*•]\s+", "• ", ln) for ln in text.splitlines()]
        lines = [re.sub(r"[ \t]+", " ", ln).rstrip() for ln in lines]
        return "\n".join(lines).strip()

    @staticmethod
    def _fmt_date(m: re.Match) -> str:
        day, mon, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= day <= 31 and 1 <= mon <= 12):
            return m.group(0)
        return f"{day:02d}-{_MONTHS[mon - 1]}-{year}"


class DoctorEdits(EditSource):
    """The production source: real clinician edits, supplied as ``{section_key: edited_text}``.
    A section the clinician left untouched returns the draft unchanged."""

    def __init__(self, edits: dict[str, str]):
        self._edits = dict(edits)

    def edit(self, section_key: str, draft_text: str) -> str:
        return self._edits.get(section_key, draft_text)
