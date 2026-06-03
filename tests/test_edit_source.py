"""The simulated reviewer is deterministic and edits presentation only: it expands abbreviations,
reformats dates and units, and leaves clinical content (numbers, diagnoses, flags) untouched.
The production DoctorEdits source honours the same interface."""

from discharge_agent.learning.edit_source import DoctorEdits, SimulatedReviewer
from discharge_agent.learning.metrics import safety_retained


def test_reviewer_is_deterministic():
    r = SimulatedReviewer()
    text = "Tab Pantoprazole 40mg BD from 26/02/2026"
    assert r.edit("discharge_medications", text) == r.edit("discharge_medications", text)


def test_reviewer_expands_abbreviations_and_formats():
    r = SimulatedReviewer()
    out = r.edit("discharge_medications", "Tab Pantoprazole 40mg BD")
    assert "Tablet" in out and "twice daily" in out and "40 mg" in out
    assert "Tab " not in out and "BD" not in out


def test_reviewer_reformats_dates():
    r = SimulatedReviewer()
    assert "26-Feb-2026" in r.edit("admission_date", "Admission 26/02/2026")


def test_reviewer_edits_are_style_only_and_pass_safety_gate():
    # A reviewer's own output must be a safe restyle of its input: same numbers, same markers.
    r = SimulatedReviewer()
    ref = "Principal Diagnosis: CONFLICT. Tab Oflox 200mg on 26/02/2026 (page 3)."
    assert safety_retained(ref, r.edit("principal_diagnosis", ref)) is True


def test_reviewer_preserves_safety_markers():
    r = SimulatedReviewer()
    out = r.edit("procedures", "Procedures: NOT DOCUMENTED in any source.")
    assert "NOT DOCUMENTED" in out


def test_doctor_edits_source_returns_real_edits_or_passthrough():
    src = DoctorEdits({"allergies": "No known drug allergies."})
    assert src.edit("allergies", "NKDA") == "No known drug allergies."
    assert src.edit("procedures", "NOT DOCUMENTED") == "NOT DOCUMENTED"
