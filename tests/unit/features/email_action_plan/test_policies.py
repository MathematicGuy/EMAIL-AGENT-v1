from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain import Priority
from cowork_agent.features.email_action_plan.policies import (
    action_fingerprint,
    calculate_priority,
    normalize_query,
    validate_max_emails,
)


def test_query_is_always_read_only_unread_inbox_scope() -> None:
    query = normalize_query("from:boss@example.com")
    assert "is:unread" in query
    assert "in:inbox" in query


def test_query_does_not_duplicate_required_terms() -> None:
    assert normalize_query("is:unread in:inbox") == "is:unread in:inbox"


@pytest.mark.parametrize("value", [0, 501])
def test_max_emails_rejects_values_outside_contract(value: int) -> None:
    with pytest.raises(ValueError):
        validate_max_emails(value)


def test_priority_policy_at_deadline_thresholds() -> None:
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    assert calculate_priority(now + timedelta(hours=24), now)[0] is Priority.URGENT
    assert calculate_priority(now + timedelta(hours=72), now)[0] is Priority.HIGH
    assert calculate_priority(None, now)[0] is Priority.MEDIUM
    assert calculate_priority(None, now, required=False)[0] is Priority.LOW


def test_priority_policy_uses_operational_impact() -> None:
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    assert calculate_priority(None, now, impact="production_blocked")[0] is Priority.HIGH
    assert calculate_priority(None, now, impact="data_loss_risk")[0] is Priority.URGENT


def test_fingerprint_is_stable_for_punctuation_and_accents() -> None:
    a = action_fingerprint("mbx", "thread", "Gửi báo cáo!", None)
    b = action_fingerprint("mbx", "thread", "gui bao cao", None)
    assert a == b
