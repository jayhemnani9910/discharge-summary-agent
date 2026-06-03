"""The draft state: the structured discharge summary as the agent builds it.

This module encodes the central safety property in the *type system*, not just in a
prompt: every section is either

* a list of SourcedValue (each carrying the source page and a verbatim quote), or
* an explicit non-value status (MISSING / PENDING / CONFLICT / NOT_DOCUMENTED).

There is deliberately no way to give a section a value without a citation. That is
what makes "no fabrication" enforceable in code (see guardrails.py).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum


class FieldStatus(str, Enum):
    EMPTY = "EMPTY"                  # not yet attempted by the agent
    VALUE = "VALUE"                  # has at least one sourced value
    MISSING = "MISSING"             # required, but absent from every document
    PENDING = "PENDING"             # result awaited / not yet available
    CONFLICT = "CONFLICT"           # sources disagree; multiple values kept
    NOT_DOCUMENTED = "NOT_DOCUMENTED"  # explicitly not recorded in the notes


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def _norm(text: str) -> str:
    """Loose normalisation for comparing two values for conflict detection."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


@dataclass
class SourcedValue:
    value: str
    source_page: int
    quote: str
    confidence: str = "medium"          # high | medium | low
    verified: bool = False              # set by the post-hoc verifier
    verifier_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SectionField:
    key: str
    status: FieldStatus = FieldStatus.EMPTY
    values: list[SourcedValue] = field(default_factory=list)
    detail: str = ""                    # explanation for a non-value status

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "status": self.status.value,
            "values": [v.to_dict() for v in self.values],
            "detail": self.detail,
        }


@dataclass
class Flag:
    field: str
    issue_type: str          # pending | missing | conflict | med_reconciliation |
                             # drug_interaction | unreadable | safety | control
    detail: str
    severity: Severity = Severity.MEDIUM
    source_pages: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class MedItem:
    stage: str               # "admission" or "discharge"
    name: str
    details: str             # dose / frequency / route as documented
    source_page: int
    quote: str
    verified: bool = True    # set False by the post-hoc verifier if unsupported by its page

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MedChange:
    name: str
    change_type: str         # added | stopped | changed | continued
    admission_detail: str
    discharge_detail: str
    reason: str
    reason_documented: bool
    source_pages: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class DraftState:
    """Mutable container the tools write into during the agent loop."""

    def __init__(self, patient_id: str, section_keys: tuple[str, ...]):
        self.patient_id = patient_id
        self.fields: dict[str, SectionField] = {k: SectionField(k) for k in section_keys}
        self.flags: list[Flag] = []
        self.medications: list[MedItem] = []
        self.med_changes: list[MedChange] = []
        self.interaction_checks: list[dict] = []   # record of drug-interaction lookups
        # Whether the agent has actually performed the two mandatory safety steps.
        # finalize is blocked until these are done when discharge meds are present.
        self.reconciliation_attempted = False
        self.interaction_check_done = False
        self.finalized = False
        self.incomplete_reason = ""

    # --- field mutations ----------------------------------------------------
    def record_value(
        self, section: str, value: str, source_page: int, quote: str, confidence: str
    ) -> SectionField:
        from .schema import SECTION_BY_KEY

        f = self.fields[section]
        sv = SourcedValue(value=value, source_page=source_page, quote=quote, confidence=confidence)
        # Conflict detection applies to single-valued *factual* sections: if a different
        # value was already recorded, keep both and mark CONFLICT rather than overwrite.
        # Narrative sections (e.g. hospital_course) are exempt: they are built from several
        # separately-cited clauses, so a second clause is an addition, not a contradiction.
        narrative = SECTION_BY_KEY[section].narrative
        if f.values and self._is_single(section) and not narrative:
            already = {_norm(v.value) for v in f.values}
            if _norm(value) not in already:
                f.values.append(sv)
                f.status = FieldStatus.CONFLICT
                return f
            return f  # duplicate of existing value; ignore
        f.values.append(sv)
        if f.status != FieldStatus.CONFLICT:
            # An item recorded into pending_results is, by definition, an awaited result:
            # keep the cited item but mark the section PENDING (not VALUE) so the status
            # matches what the field actually is.
            f.status = FieldStatus.PENDING if section == "pending_results" else FieldStatus.VALUE
        return f

    def set_status(self, section: str, status: FieldStatus, detail: str) -> None:
        f = self.fields[section]
        f.status = status
        f.detail = detail

    @staticmethod
    def _is_single(section: str) -> bool:
        from .schema import SECTION_BY_KEY

        return SECTION_BY_KEY[section].cardinality == "single"

    # --- flags & meds -------------------------------------------------------
    def add_flag(
        self, field_name: str, issue_type: str, detail: str, severity: Severity, source_pages=None
    ) -> Flag:
        flag = Flag(
            field=field_name,
            issue_type=issue_type,
            detail=detail,
            severity=severity,
            source_pages=list(source_pages or []),
        )
        self.flags.append(flag)
        return flag

    def add_medication(self, stage: str, name: str, details: str, source_page: int, quote: str):
        self.medications.append(
            MedItem(stage=stage, name=name, details=details, source_page=source_page, quote=quote)
        )

    def meds_for(self, stage: str) -> list[MedItem]:
        return [m for m in self.medications if m.stage == stage]

    # --- completeness checks (used by the finalize guardrail) ---------------
    def unhandled_sections(self) -> list[str]:
        """Sections the agent has not yet addressed at all (still EMPTY)."""
        return [k for k, f in self.fields.items() if f.status == FieldStatus.EMPTY]

    # --- serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "finalized": self.finalized,
            "incomplete_reason": self.incomplete_reason,
            "fields": {k: f.to_dict() for k, f in self.fields.items()},
            "medications": [m.to_dict() for m in self.medications],
            "medication_changes": [c.to_dict() for c in self.med_changes],
            "interaction_checks": self.interaction_checks,
            "flags": [fl.to_dict() for fl in self.flags],
        }
