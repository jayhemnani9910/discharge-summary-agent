"""The Part 2 reward signal and safety gate: edit burden is a bounded section-level distance,
and the gate refuses any restyling that fabricates, drops, or hides a clinical fact."""

from discharge_agent.learning.metrics import (
    draft_burden,
    edit_burden,
    safety_retained,
)


def test_identical_text_has_zero_burden():
    assert edit_burden("Tablet Pantoprazole 40mg once daily", "Tablet Pantoprazole 40mg once daily") == 0.0


def test_burden_is_bounded_and_ordered():
    low = edit_burden("Tab Pantoprazole 40mg OD", "Tablet Pantoprazole 40mg once daily")
    high = edit_burden("Tab Pantoprazole 40mg OD", "completely different unrelated text here")
    assert 0.0 < low < high <= 1.0


def test_draft_burden_averages_sections():
    pairs = {"a": ("same", "same"), "b": ("x", "totally other")}
    b = draft_burden(pairs)
    assert 0.0 < b < 1.0


def test_safety_gate_passes_pure_restyle():
    ref = "Principal Diagnosis: CONFLICT. Acute gastroenteritis (page 1) vs DKA (page 3)."
    cand = "Principal diagnosis - CONFLICT: acute gastroenteritis [page 1] versus DKA [page 3]."
    assert safety_retained(ref, cand) is True


def test_safety_gate_allows_date_reformatting():
    # The house style rewrites 26/02/2026 -> 26-Feb-2026; that is a pure restyle, not a dropped
    # month, so the gate must accept it.
    ref = "Admission date: 26/02/2026."
    cand = "Admission date: 26-Feb-2026."
    assert safety_retained(ref, cand) is True


def test_safety_gate_blocks_dropped_conflict_marker():
    ref = "Principal Diagnosis: CONFLICT. gastroenteritis vs DKA."
    cand = "Principal diagnosis: gastroenteritis."   # CONFLICT hidden, a value dropped
    assert safety_retained(ref, cand) is False


def test_safety_gate_blocks_fabricated_number():
    ref = "Discharge medications: Pantoprazole 40 mg."
    cand = "Discharge medications: Pantoprazole 40 mg; Metformin 500 mg."   # invented 500
    assert safety_retained(ref, cand) is False


def test_safety_gate_blocks_dropped_number():
    ref = "Admission date 26 02 2026."
    cand = "Admission date documented."   # vagueness: the date numbers dropped
    assert safety_retained(ref, cand) is False
