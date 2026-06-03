"""Tool behaviour: citation enforcement, conflict surfacing, reconciliation, interactions."""

from discharge_agent.tools import ToolDispatcher
from discharge_agent.state import FieldStatus


def make(store, state, config):
    return ToolDispatcher(store, state, config)


def test_record_field_rejects_uncited_value(store, state, fast_config):
    d = make(store, state, fast_config)
    res = d.dispatch("record_field", {
        "section": "principal_diagnosis", "value": "Pneumonia",
        "source_page": 1, "quote": "Pneumonia", "confidence": "high"})
    assert res.get("rejected")            # the quote is not on page 1
    assert state.fields["principal_diagnosis"].status == FieldStatus.EMPTY


def test_record_field_accepts_cited_value(store, state, fast_config):
    d = make(store, state, fast_config)
    res = d.dispatch("record_field", {
        "section": "principal_diagnosis",
        "value": "Acute gastroenteritis with dehydration",
        "source_page": 1, "quote": "Acute gastroenteritis with dehydration",
        "confidence": "high"})
    assert res.get("ok")
    assert state.fields["principal_diagnosis"].status == FieldStatus.VALUE


def test_conflicting_values_become_conflict_and_flag(store, state, fast_config):
    d = make(store, state, fast_config)
    d.dispatch("record_field", {"section": "principal_diagnosis",
        "value": "Acute gastroenteritis with dehydration", "source_page": 1,
        "quote": "Acute gastroenteritis with dehydration"})
    d.dispatch("record_field", {"section": "principal_diagnosis",
        "value": "DKA", "source_page": 2, "quote": "DKA (diabetic ketoacidosis)"})
    f = state.fields["principal_diagnosis"]
    assert f.status == FieldStatus.CONFLICT
    assert len(f.values) == 2
    assert any(fl.issue_type == "conflict" for fl in state.flags)


def test_pending_results_value_is_marked_pending(store, state, fast_config):
    # Recording an awaited investigation into pending_results keeps the cited item but
    # marks the section PENDING (not VALUE): a pending lab must read as pending.
    d = make(store, state, fast_config)
    res = d.dispatch("record_field", {
        "section": "pending_results", "value": "Urine culture, report awaited",
        "source_page": 1, "quote": "Urine culture sent, report awaited", "confidence": "high"})
    assert res.get("ok")
    f = state.fields["pending_results"]
    assert f.status == FieldStatus.PENDING
    assert len(f.values) == 1


def test_hospital_course_clauses_accumulate_without_conflict(store, state, fast_config):
    # hospital_course is narrative: several separately-cited clauses are additions, not a
    # contradiction, so the section stays VALUE rather than flipping to CONFLICT.
    d = make(store, state, fast_config)
    d.dispatch("record_field", {"section": "hospital_course",
        "value": "Admitted with acute gastroenteritis and dehydration.",
        "source_page": 1, "quote": "Acute gastroenteritis with dehydration"})
    d.dispatch("record_field", {"section": "hospital_course",
        "value": "ER work-up showed DKA with blood sugar 443 mg/dl.",
        "source_page": 2, "quote": "Blood sugar 443 mg/dl"})
    f = state.fields["hospital_course"]
    assert f.status == FieldStatus.VALUE
    assert len(f.values) == 2
    assert not any(fl.issue_type == "conflict" for fl in state.flags)


def test_note_unavailable_sets_status_and_flag(store, state, fast_config):
    d = make(store, state, fast_config)
    d.dispatch("note_unavailable", {"section": "pending_results", "status": "PENDING",
        "detail": "urine culture awaited", "source_pages": [1]})
    assert state.fields["pending_results"].status == FieldStatus.PENDING
    assert any(fl.issue_type == "pending" for fl in state.flags)


def test_reconciliation_flags_change_without_reason(store, state, fast_config):
    d = make(store, state, fast_config)
    # Admission has insulin; discharge does not -> "stopped" with no documented reason.
    d.dispatch("record_medication", {"stage": "admission", "name": "Insulin",
        "details": "10U", "source_page": 2, "quote": "Insulin 10U"})
    d.dispatch("record_medication", {"stage": "discharge", "name": "Raciper",
        "details": "40mg 1-0-0", "source_page": 1, "quote": "Tab Raciper 40mg 1-0-0"})
    res = d.dispatch("reconcile_medications", {})
    assert res["ok"]
    assert "insulin" in [c["name"] for c in res["changes"]]
    assert any("insulin" in name for name in res["flagged_no_reason"])
    assert any(fl.issue_type == "med_reconciliation" for fl in state.flags)
    assert state.reconciliation_attempted


def test_reconciliation_reason_suppresses_flag(store, state, fast_config):
    d = make(store, state, fast_config)
    d.dispatch("record_medication", {"stage": "admission", "name": "Insulin",
        "details": "10U", "source_page": 2, "quote": "Insulin 10U"})
    d.dispatch("record_medication", {"stage": "discharge", "name": "Raciper",
        "details": "40mg 1-0-0", "source_page": 1, "quote": "Tab Raciper 40mg 1-0-0"})
    # A quote-backed reason for stopping insulin (the quote must be on the cited page).
    res = d.dispatch("reconcile_medications", {"documented_reasons": [
        {"medication": "Insulin", "reason": "blood sugar controlled",
         "source_page": 2, "quote": "Blood sugar 443 mg/dl"}]})
    insulin = [c for c in res["changes"] if c["name"] == "insulin"][0]
    assert insulin["reason_documented"] is True
    assert "insulin" not in [n for n in res["flagged_no_reason"]]


def test_generic_merges_ocr_variants_but_keeps_distinct_drugs():
    from discharge_agent.tools import _generic
    # OCR/format variants of one drug collapse to one generic...
    assert _generic("INJ MEROMAC (Meropenem)") == _generic("INJ MEROPDAC") == "meropenem"
    assert _generic("TAB DOLO") == _generic("T DOLO")
    assert _generic("INJ HAPPYNERVE PLUS") == _generic("INJ HAPPY NERVE PLUS")
    assert _generic("INJ SUMOL") == _generic("INJ SOMOL")
    # ...while genuinely different drugs stay separate.
    assert _generic("INJ H. ACTRAPID") != _generic("INJ LANTUS")


def test_generic_does_not_confuse_substring_brand_names():
    # A brand key must match a whole word, not a substring: "oflox" is a substring of
    # "ciprofloxacin" but ciprofloxacin is a different drug and must not collapse to ofloxacin.
    from discharge_agent.tools import _generic
    assert _generic("Tab Ciprofloxacin 500mg") == "ciprofloxacin"
    assert _generic("Ciprofloxacin") != _generic("Oflox")
    assert _generic("TAB. OFLOX TZ") == "ofloxacin"
    assert _generic("Levofloxacin") == "levofloxacin"


def test_reconciliation_merges_duplicate_brand_variants(store, state, fast_config):
    # Meromac and Meropdac are the same drug under two transcribed spellings; reconciliation
    # must report meropenem stopped once, not twice.
    d = make(store, state, fast_config)
    d.dispatch("record_medication", {"stage": "admission", "name": "INJ MEROMAC (Meropenem)",
        "details": "1g", "source_page": 2, "quote": "Insulin 10U"})
    d.dispatch("record_medication", {"stage": "admission", "name": "INJ MEROPDAC",
        "details": "1g", "source_page": 2, "quote": "Blood sugar 443 mg/dl"})
    d.dispatch("record_medication", {"stage": "discharge", "name": "Raciper",
        "details": "40mg 1-0-0", "source_page": 1, "quote": "Tab Raciper 40mg 1-0-0"})
    res = d.dispatch("reconcile_medications", {})
    meropenem_changes = [c for c in res["changes"] if c["name"] == "meropenem"]
    assert len(meropenem_changes) == 1
    assert meropenem_changes[0]["change_type"] == "stopped"


def test_interaction_check_flags_qt_pair(store, state, fast_config):
    d = make(store, state, fast_config)
    res = d.dispatch("drug_interaction_check", {"medications": ["Emeset", "Oflox"]})
    assert res["ok"]
    assert res["interactions"], "ondansetron + ofloxacin should raise a QT interaction"
    assert any(fl.issue_type == "drug_interaction" for fl in state.flags)
    assert state.interaction_check_done


def test_interaction_check_disclaimer_present(store, state, fast_config):
    d = make(store, state, fast_config)
    res = d.dispatch("drug_interaction_check", {"medications": ["Raciper"]})
    assert "NOT a guarantee" in res["disclaimer"]
