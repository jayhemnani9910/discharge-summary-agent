"""Shared test fixtures: a fake page store and a fast config (no network, no waits)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discharge_agent.config import Config
from discharge_agent.ingest import PageRecord, PageStore
from discharge_agent.state import DraftState
from discharge_agent.schema import SECTION_KEYS


PAGE1_TEXT = (
    "Name: Test Patient, 45F. MRN 0001. Admitted 01/03/2026. Discharged 05/03/2026. "
    "Diagnosis: Acute gastroenteritis with dehydration. Secondary: Type 2 diabetes mellitus. "
    "Allergies: NKDA. Hospital course: treated with IV fluids and antibiotics, improved. "
    "Discharge condition: hemodynamically stable. "
    "Discharge medications: Tab Raciper 40mg 1-0-0; Tab Emeset 4mg 1-1-1; Tab Oflox 200mg 1-0-1. "
    "Follow up on 09/03/2026. Urine culture sent, report awaited."
)
PAGE2_TEXT = (
    "ER chart. Diagnosis: DKA (diabetic ketoacidosis). Admission medications: "
    "Insulin 10U; Tab Raciper 40mg 1-0-0. Blood sugar 443 mg/dl."
)


@pytest.fixture
def store():
    return PageStore([
        PageRecord(page=1, readable=True, text=PAGE1_TEXT, doc_type="discharge_summary",
                   dates=["01/03/2026", "05/03/2026"], gist="discharge summary"),
        PageRecord(page=2, readable=True, text=PAGE2_TEXT, doc_type="er_observation",
                   dates=["01/03/2026"], has_handwriting=True, gist="ER chart, DKA"),
        PageRecord(page=3, readable=False, text="", doc_type="unreadable",
                   error="injected", gist=""),
    ])


@pytest.fixture
def fast_config():
    cfg = Config()
    cfg.chat_provider = "mock"
    cfg.vision_provider = "mock"
    cfg.llm_max_retries = 2
    cfg.llm_backoff_base_seconds = 0.0   # no real sleeping in tests
    cfg.max_steps = 25
    return cfg


@pytest.fixture
def state():
    return DraftState("test-patient", SECTION_KEYS)
