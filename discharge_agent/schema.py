"""The required structure of a discharge summary draft.

These are exactly the sections the assignment requires as output. Each section
declares whether it holds a single value or a list of items, plus a short hint that
is shown to the agent so it knows what to look for. Keeping this in one place means
the loop, the renderer, and the finalize guardrail all agree on what "complete" means.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    cardinality: str  # "single" or "list"
    hint: str
    narrative: bool = False  # a synthesized summary (e.g. hospital course), not a single fact


SECTIONS: tuple[Section, ...] = (
    Section("patient_demographics", "Patient Demographics", "single",
            "name/ID, age, sex, ward/bed if present"),
    Section("admission_date", "Admission Date", "single", "date of admission"),
    Section("discharge_date", "Discharge Date", "single", "date of discharge"),
    Section("principal_diagnosis", "Principal Diagnosis", "single",
            "the main diagnosis treated this stay; flag if notes disagree"),
    Section("secondary_diagnoses", "Secondary Diagnoses", "list",
            "other active problems / comorbidities"),
    Section("hospital_course", "Hospital Course", "single",
            "concise narrative of what happened during the stay", narrative=True),
    Section("procedures", "Procedures", "list", "procedures/interventions performed"),
    Section("discharge_medications", "Discharge Medications", "list",
            "each discharge medication with dose and frequency"),
    Section("allergies", "Allergies", "single", "known drug allergies, or NKDA if stated"),
    Section("follow_up_instructions", "Follow-up Instructions", "list",
            "appointments, review dates, return precautions"),
    Section("pending_results", "Pending Results", "list",
            "labs/investigations sent but not yet resulted at discharge"),
    Section("discharge_condition", "Discharge Condition", "single",
            "the patient's condition/status at discharge"),
)

SECTION_KEYS: tuple[str, ...] = tuple(s.key for s in SECTIONS)
SECTION_BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}
