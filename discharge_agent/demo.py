"""A self-contained, no-API demonstration.

``python -m discharge_agent demo`` runs the full pipeline (loop, tools, guardrails,
verifier, rendering) on a tiny SYNTHETIC record using the deterministic mock provider.
It needs no API key and produces real draft/flags/trace files, so a reviewer can see
the system run end-to-end in one command. The synthetic record deliberately contains a
diagnosis conflict, a pending lab, an undocumented medication change, a drug
interaction, and an unreadable page.
"""

from __future__ import annotations

import json
import re

from .ingest import PageRecord, PageStore
from .llm.base import LLMResponse, ToolCall
from .llm.mock import MockProvider

_PAGE1 = (
    "DISCHARGE SUMMARY. Name: Jane Doe, 52F. MRN DEMO-1. "
    "Admitted 01/03/2026. Discharged 05/03/2026. "
    "Diagnosis: Acute gastroenteritis with dehydration. "
    "Secondary: Type 2 diabetes mellitus. Allergies: NKDA. "
    "Hospital course: treated with IV fluids and antibiotics; clinically improved. "
    "Discharge condition: hemodynamically stable. "
    "Discharge medications: Tab Raciper 40mg 1-0-0; Tab Emeset 4mg 1-1-1; Tab Oflox 200mg 1-0-1. "
    "Follow up on 09/03/2026. Urine culture sent, report awaited."
)
_PAGE2 = (
    "ER OBSERVATION CHART. Diagnosis: DKA (diabetic ketoacidosis). "
    "Admission medications: Insulin 10U; Tab Raciper 40mg 1-0-0. Blood sugar 443 mg/dl."
)


def build_demo_store() -> PageStore:
    return PageStore([
        PageRecord(page=1, readable=True, text=_PAGE1, doc_type="discharge_summary",
                   dates=["01/03/2026", "05/03/2026"], gist="typed discharge summary"),
        PageRecord(page=2, readable=True, text=_PAGE2, doc_type="er_observation",
                   dates=["01/03/2026"], has_handwriting=True, gist="handwritten ER chart (DKA)"),
        PageRecord(page=3, readable=False, text="", doc_type="unreadable",
                   error="illegible scan", gist=""),
    ])


def _resp(*calls):
    return LLMResponse(text="(demo) deciding next action", tool_calls=[ToolCall(n, a) for n, a in calls])


def _all_supported(system, prompt, json_mode):
    m = re.search(r"CLAIMS:\s*(\[.*\])", prompt, re.DOTALL)
    idxs = [0]
    if m:
        try:
            idxs = [c["index"] for c in json.loads(m.group(1))]
        except Exception:
            pass
    return json.dumps({"verdicts": [{"index": i, "verdict": "supported", "note": "demo"} for i in idxs]})


def build_demo_provider(config) -> MockProvider:
    script = [
        _resp(("read_page", {"page": 1}), ("read_page", {"page": 2})),
        _resp(
            ("record_field", {"section": "patient_demographics", "value": "Jane Doe, 52F (MRN DEMO-1)",
                              "source_page": 1, "quote": "Name: Jane Doe, 52F"}),
            ("record_field", {"section": "admission_date", "value": "01/03/2026",
                              "source_page": 1, "quote": "Admitted 01/03/2026"}),
            ("record_field", {"section": "discharge_date", "value": "05/03/2026",
                              "source_page": 1, "quote": "Discharged 05/03/2026"}),
            ("record_field", {"section": "principal_diagnosis",
                              "value": "Acute gastroenteritis with dehydration", "source_page": 1,
                              "quote": "Acute gastroenteritis with dehydration"}),
            ("record_field", {"section": "secondary_diagnoses", "value": "Type 2 diabetes mellitus",
                              "source_page": 1, "quote": "Secondary: Type 2 diabetes mellitus"}),
            ("record_field", {"section": "hospital_course",
                              "value": "IV fluids and antibiotics; clinically improved", "source_page": 1,
                              "quote": "treated with IV fluids and antibiotics; clinically improved"}),
            ("record_field", {"section": "allergies", "value": "NKDA",
                              "source_page": 1, "quote": "Allergies: NKDA"}),
            ("record_field", {"section": "follow_up_instructions", "value": "Review on 09/03/2026",
                              "source_page": 1, "quote": "Follow up on 09/03/2026"}),
            ("record_field", {"section": "discharge_condition", "value": "hemodynamically stable",
                              "source_page": 1, "quote": "Discharge condition: hemodynamically stable"}),
            ("record_field", {"section": "pending_results", "value": "Urine culture - report awaited",
                              "source_page": 1, "quote": "Urine culture sent, report awaited"}),
        ),
        _resp(
            ("record_field", {"section": "principal_diagnosis", "value": "DKA (diabetic ketoacidosis)",
                              "source_page": 2, "quote": "DKA (diabetic ketoacidosis)"}),
            ("note_unavailable", {"section": "procedures", "status": "NOT_DOCUMENTED",
                                  "detail": "no procedures documented in the notes"}),
            ("record_medication", {"stage": "discharge", "name": "Raciper", "details": "40mg 1-0-0",
                                   "source_page": 1, "quote": "Tab Raciper 40mg 1-0-0"}),
            ("record_medication", {"stage": "discharge", "name": "Emeset", "details": "4mg 1-1-1",
                                   "source_page": 1, "quote": "Tab Emeset 4mg 1-1-1"}),
            ("record_medication", {"stage": "discharge", "name": "Oflox", "details": "200mg 1-0-1",
                                   "source_page": 1, "quote": "Tab Oflox 200mg 1-0-1"}),
            ("record_medication", {"stage": "admission", "name": "Insulin", "details": "10U",
                                   "source_page": 2, "quote": "Insulin 10U"}),
            ("record_medication", {"stage": "admission", "name": "Raciper", "details": "40mg 1-0-0",
                                   "source_page": 2, "quote": "Tab Raciper 40mg 1-0-0"}),
        ),
        _resp(
            ("reconcile_medications", {}),
            ("drug_interaction_check", {"medications": ["Raciper", "Emeset", "Oflox"]}),
        ),
        _resp(("finalize_draft", {})),
    ]
    return MockProvider(config, script=script, completer=_all_supported)
