"""The no-fabrication guardrail: a value needs a real, on-page citation."""

from discharge_agent.guardrails import quote_supported, finalize_check
from discharge_agent.state import FieldStatus, Severity


def test_quote_present_is_supported(store):
    ok, _ = quote_supported(store, 1, "Acute gastroenteritis with dehydration")
    assert ok


def test_quote_absent_is_rejected(store):
    ok, reason = quote_supported(store, 1, "myocardial infarction")
    assert not ok
    assert "not found" in reason


def test_unreadable_page_cannot_be_cited(store):
    ok, reason = quote_supported(store, 3, "anything")
    assert not ok
    assert "unreadable" in reason


def test_missing_page_rejected(store):
    ok, reason = quote_supported(store, 99, "x")
    assert not ok


def test_too_short_quote_rejected(store):
    ok, reason = quote_supported(store, 1, "a")
    assert not ok


def test_finalize_blocked_until_all_sections_addressed(store, state):
    ready, remaining = finalize_check(state, store)
    assert not ready
    assert remaining  # every section is still EMPTY


def test_finalize_ready_when_complete(store, state):
    for key in state.fields:
        state.set_status(key, FieldStatus.NOT_DOCUMENTED, "n/a")
    # No discharge meds recorded, so reconciliation/interaction are not required.
    # But the unreadable page 3 must be flagged before finalize is allowed.
    state.add_flag("source_document", "unreadable", "page 3", Severity.HIGH, [3])
    ready, remaining = finalize_check(state, store)
    assert ready, remaining
