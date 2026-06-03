"""The post-hoc verifier removes values the source does not actually support,
and never treats its own failure as success."""

import json

from discharge_agent.llm.mock import MockProvider
from discharge_agent.state import FieldStatus
from discharge_agent.tools import ToolDispatcher
from discharge_agent.verify import verify_draft


def _seed_two_values(store, state):
    d = ToolDispatcher(store, state, None)
    d.dispatch("record_field", {"section": "principal_diagnosis",
        "value": "Acute gastroenteritis with dehydration", "source_page": 1,
        "quote": "Acute gastroenteritis with dehydration"})
    d.dispatch("record_field", {"section": "discharge_condition",
        "value": "hemodynamically stable", "source_page": 1,
        "quote": "Discharge condition: hemodynamically stable"})


def test_verifier_downgrades_unsupported_value(store, state, fast_config):
    _seed_two_values(store, state)

    def completer(system, prompt, json_mode):
        # Parse the claim values and judge each: the discharge-condition value is
        # deemed unsupported, the diagnosis value supported.
        import re
        claims = json.loads(re.search(r"CLAIMS:\s*(\[.*\])", prompt, re.DOTALL).group(1))
        verdicts = []
        for c in claims:
            v = "unsupported" if "stable" in c["value"].lower() else "supported"
            verdicts.append({"index": c["index"], "verdict": v, "note": "test"})
        return json.dumps({"verdicts": verdicts})

    provider = MockProvider(fast_config, completer=completer)
    summary = verify_draft(provider, store, state, fast_config)

    assert summary["values_downgraded"] == 1
    assert state.fields["principal_diagnosis"].values[0].verified is True
    # The unsupported value was removed and the section flagged.
    assert state.fields["discharge_condition"].status == FieldStatus.MISSING
    assert not state.fields["discharge_condition"].values
    assert any(fl.issue_type == "safety" for fl in state.flags)


def test_verifier_failure_is_not_silent_success(store, state, fast_config):
    _seed_two_values(store, state)

    def completer(system, prompt, json_mode):
        raise RuntimeError("verifier offline")

    provider = MockProvider(fast_config, completer=completer)
    verify_draft(provider, store, state, fast_config)

    # Values are kept but explicitly marked unverified, with a flag demanding manual review.
    assert state.fields["principal_diagnosis"].values[0].verified is False
    assert any("verification could not run" in fl.detail.lower() for fl in state.flags)


def test_verifier_checks_narrative_clauses_not_blanket_skip(store, state, fast_config):
    # hospital_course is narrative; each clause must still be verified against its page (an
    # unsupported clause is dropped), while the section keeps a "synthesized, review" flag.
    d = ToolDispatcher(store, state, None)
    d.dispatch("record_field", {"section": "hospital_course",
        "value": "Admitted with gastroenteritis and dehydration.", "source_page": 1,
        "quote": "Acute gastroenteritis with dehydration"})
    d.dispatch("record_field", {"section": "hospital_course",
        "value": "ER work-up showed DKA, blood sugar 443.", "source_page": 2,
        "quote": "Blood sugar 443 mg/dl"})

    def completer(system, prompt, json_mode):
        import re
        claims = json.loads(re.search(r"CLAIMS:\s*(\[.*\])", prompt, re.DOTALL).group(1))
        return json.dumps({"verdicts": [
            {"index": c["index"],
             "verdict": "unsupported" if "443" in c["value"] else "supported", "note": "t"}
            for c in claims]})

    provider = MockProvider(fast_config, completer=completer)
    verify_draft(provider, store, state, fast_config)
    f = state.fields["hospital_course"]
    assert len(f.values) == 1 and "gastroenteritis" in f.values[0].value.lower()
    assert any(fl.issue_type == "safety" and "synthes" in fl.detail.lower() for fl in state.flags)


def test_verifier_flags_unsupported_medication(store, state, fast_config):
    # Medications are now re-checked against their cited page; an unsupported one is flagged.
    d = ToolDispatcher(store, state, None)
    d.dispatch("record_medication", {"stage": "discharge", "name": "Raciper",
        "details": "40mg 1-0-0", "source_page": 1, "quote": "Tab Raciper 40mg 1-0-0"})

    def completer(system, prompt, json_mode):
        import re
        claims = json.loads(re.search(r"CLAIMS:\s*(\[.*\])", prompt, re.DOTALL).group(1))
        return json.dumps({"verdicts": [
            {"index": c["index"], "verdict": "unsupported", "note": "not on page"} for c in claims]})

    provider = MockProvider(fast_config, completer=completer)
    verify_draft(provider, store, state, fast_config)
    assert state.medications[0].verified is False
    assert any(fl.issue_type == "safety" and "medication" in fl.detail.lower() for fl in state.flags)


def test_conflict_scan_upgrades_single_value_to_conflict(store, state, fast_config):
    # The model recorded only one principal diagnosis; the guaranteed conflict scan finds a
    # different one (with a verbatim quote) on another page and upgrades the field to CONFLICT.
    from discharge_agent.verify import detect_conflicts
    d = ToolDispatcher(store, state, None)
    d.dispatch("record_field", {"section": "principal_diagnosis",
        "value": "Acute gastroenteritis with dehydration", "source_page": 1,
        "quote": "Acute gastroenteritis with dehydration"})
    assert state.fields["principal_diagnosis"].status == FieldStatus.VALUE

    def completer(system, prompt, json_mode):
        return json.dumps({"conflicts": [{"field": "principal_diagnosis",
            "value": "DKA (diabetic ketoacidosis)", "page": 2,
            "quote": "DKA (diabetic ketoacidosis)"}]})

    provider = MockProvider(fast_config, completer=completer)
    added = detect_conflicts(provider, store, state, fast_config)
    assert added >= 1
    assert state.fields["principal_diagnosis"].status == FieldStatus.CONFLICT
    assert any(fl.issue_type == "conflict" for fl in state.flags)


def test_conflict_scan_recovers_a_verifier_emptied_field(store, state, fast_config):
    # The verifier dropped an over-bundled principal diagnosis, leaving the field MISSING. The
    # conflict scan re-populates it from atomic, verbatim-quoted values -> a clean CONFLICT.
    from discharge_agent.verify import detect_conflicts
    state.set_status("principal_diagnosis", FieldStatus.MISSING, "removed by verifier")

    def completer(system, prompt, json_mode):
        return json.dumps({"conflicts": [
            {"field": "principal_diagnosis", "value": "Acute gastroenteritis with dehydration",
             "page": 1, "quote": "Acute gastroenteritis with dehydration"},
            {"field": "principal_diagnosis", "value": "DKA (diabetic ketoacidosis)",
             "page": 2, "quote": "DKA (diabetic ketoacidosis)"}]})

    provider = MockProvider(fast_config, completer=completer)
    detect_conflicts(provider, store, state, fast_config)
    f = state.fields["principal_diagnosis"]
    assert f.status == FieldStatus.CONFLICT
    assert len(f.values) == 2
