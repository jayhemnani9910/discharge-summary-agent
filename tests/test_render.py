"""Renderer behaviour: the human-facing draft must surface safety-relevant context,
not only a count. A drug-interaction screen used a limited mock database, and the draft
must say so (absence of a result is not a guarantee of safety)."""

from types import SimpleNamespace

from discharge_agent.render import render_draft_md
from discharge_agent.state import Severity


def _result(state, finalized=True):
    return SimpleNamespace(state=state, finalized=finalized, steps=1,
                           stop_reason="finalized by agent")


def test_draft_shows_interaction_limited_coverage_disclaimer(state):
    # An interaction check ran but found nothing; the draft must still warn that the
    # screen is limited, so "no interaction" is not read as "safe".
    state.interaction_check_done = True
    state.interaction_checks.append({"input": ["Oflox", "Emeset"], "interactions": []})
    md = render_draft_md(_result(state))
    assert "not a guarantee" in md.lower()
