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
