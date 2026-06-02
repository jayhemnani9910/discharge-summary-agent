"""End-to-end agent loop on a fake store (no PDF, no network): happy path, the hard
step cap, and graceful handling when the LLM is unavailable."""

import json
import re

from discharge_agent.agent import run_agent
from discharge_agent.llm.base import LLMResponse, ToolCall, TransientLLMError
from discharge_agent.llm.mock import MockProvider
from discharge_agent.state import FieldStatus
from discharge_agent.trace import Tracer


def _resp(*calls):
    return LLMResponse(text="step reasoning", tool_calls=[ToolCall(n, a) for (n, a) in calls])


def _all_supported(system, prompt, json_mode):
    m = re.search(r"CLAIMS:\s*(\[.*\])", prompt, re.DOTALL)
    idxs = [0]
    if m:
        try:
            idxs = [c["index"] for c in json.loads(m.group(1))]
        except Exception:
            pass
    return json.dumps({"verdicts": [{"index": i, "verdict": "supported", "note": "ok"} for i in idxs]})


def _happy_script():
    return [
        _resp(("read_page", {"page": 1}), ("read_page", {"page": 2})),
        _resp(
            ("record_field", {"section": "patient_demographics", "value": "Test Patient, 45F",
                              "source_page": 1, "quote": "Name: Test Patient, 45F"}),
            ("record_field", {"section": "admission_date", "value": "01/03/2026",
                              "source_page": 1, "quote": "Admitted 01/03/2026"}),
            ("record_field", {"section": "discharge_date", "value": "05/03/2026",
                              "source_page": 1, "quote": "Discharged 05/03/2026"}),
            ("record_field", {"section": "principal_diagnosis",
                              "value": "Acute gastroenteritis with dehydration",
                              "source_page": 1, "quote": "Acute gastroenteritis with dehydration"}),
            ("record_field", {"section": "secondary_diagnoses", "value": "Type 2 diabetes mellitus",
                              "source_page": 1, "quote": "Secondary: Type 2 diabetes mellitus"}),
            ("record_field", {"section": "hospital_course",
                              "value": "IV fluids and antibiotics, improved", "source_page": 1,
                              "quote": "treated with IV fluids and antibiotics, improved"}),
            ("record_field", {"section": "allergies", "value": "NKDA",
                              "source_page": 1, "quote": "Allergies: NKDA"}),
            ("record_field", {"section": "follow_up_instructions", "value": "Review 09/03/2026",
                              "source_page": 1, "quote": "Follow up on 09/03/2026"}),
            ("record_field", {"section": "discharge_condition", "value": "hemodynamically stable",
                              "source_page": 1, "quote": "Discharge condition: hemodynamically stable"}),
            ("record_field", {"section": "pending_results", "value": "Urine culture - awaited",
                              "source_page": 1, "quote": "Urine culture sent, report awaited"}),
        ),
        _resp(
            # Second, conflicting principal diagnosis from the ER chart -> CONFLICT.
            ("record_field", {"section": "principal_diagnosis", "value": "DKA",
                              "source_page": 2, "quote": "DKA (diabetic ketoacidosis)"}),
            ("note_unavailable", {"section": "procedures", "status": "NOT_DOCUMENTED",
                                  "detail": "no procedures documented"}),
            ("record_medication", {"stage": "discharge", "name": "Raciper", "details": "40mg 1-0-0",
                                   "source_page": 1, "quote": "Tab Raciper 40mg 1-0-0"}),
            ("record_medication", {"stage": "discharge", "name": "Emeset", "details": "4mg 1-1-1",
                                   "source_page": 1, "quote": "Tab Emeset 4mg 1-1-1"}),
            ("record_medication", {"stage": "discharge", "name": "Oflox", "details": "200mg 1-0-1",
                                   "source_page": 1, "quote": "Tab Oflox 200mg 1-0-1"}),
            ("record_medication", {"stage": "admission", "name": "Insulin", "details": "10U",
                                   "source_page": 2, "quote": "Insulin 10U"}),
        ),
        _resp(
            ("reconcile_medications", {}),
            ("drug_interaction_check", {"medications": ["Raciper", "Emeset", "Oflox"]}),
        ),
        _resp(("finalize_draft", {})),
    ]


def test_happy_path_finalizes_with_conflict_and_interaction(store, fast_config):
    provider = MockProvider(fast_config, script=_happy_script(), completer=_all_supported)
    tracer = Tracer()
    result = run_agent(None, "test-patient", provider, fast_config, tracer, store=store)

    assert result.finalized is True
    assert result.stop_reason == "finalized by agent"
    # The diagnosis conflict was surfaced, not resolved.
    assert result.state.fields["principal_diagnosis"].status == FieldStatus.CONFLICT
    issue_types = {fl.issue_type for fl in result.state.flags}
    assert "conflict" in issue_types
    assert "drug_interaction" in issue_types       # ondansetron + ofloxacin QT risk
    assert "med_reconciliation" in issue_types      # insulin stopped, no reason
    assert "unreadable" in issue_types              # page 3
    # Trace recorded steps.
    assert any(ev.kind == "step" for ev in tracer.events)
    assert any(ev.kind == "finalize" for ev in tracer.events)


def test_step_cap_produces_partial_draft_not_crash(store, fast_config):
    fast_config.max_steps = 4

    def responder(system, history, tools):
        return _resp(("read_page", {"page": 1}))   # never finalizes

    provider = MockProvider(fast_config, responder=responder)
    tracer = Tracer()
    result = run_agent(None, "test-patient", provider, fast_config, tracer, store=store)

    assert result.finalized is False
    assert result.steps == 4
    # Every section forced to an explicit MISSING (nothing silently blank), and flagged.
    assert all(f.status == FieldStatus.MISSING for f in result.state.fields.values())
    assert any(fl.issue_type == "missing" for fl in result.state.flags)


def test_llm_unavailable_is_handled_gracefully(store, fast_config):
    provider = MockProvider(fast_config, script=[TransientLLMError("429")] * 3)
    tracer = Tracer()
    result = run_agent(None, "test-patient", provider, fast_config, tracer, store=store)

    assert result.finalized is False
    assert "unavailable" in result.stop_reason
    # Still produced a (partial) draft with sections flagged; never raised.
    assert result.state.fields["patient_demographics"].status == FieldStatus.MISSING
