"""Frozen behavior: query guard can narrow but never broaden; max_emails clamp."""

import asyncio

import pytest

from cowork_agent.features.email_action_plan.policies import (
    DEFAULT_QUERY,
    normalize_query,
    validate_max_emails,
)


def test_default_query_is_unread_inbox() -> None:
    assert normalize_query(None) == DEFAULT_QUERY == "is:unread in:inbox"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "is:read",
        "in:all",
        "in:sent label:urgent",
        "from:boss@company.com",
        "subject:báo cáo",
    ],
)
def test_broadening_attempts_always_keep_unread_inbox_guard(query: str) -> None:
    normalized = normalize_query(query)
    terms = normalized.lower().split()
    assert "is:unread" in terms
    assert "in:inbox" in terms


def test_narrowing_terms_survive_normalization() -> None:
    normalized = normalize_query("label:dự-án is:unread in:inbox newer_than:2d")
    terms = normalized.split()
    assert "label:dự-án" in terms
    assert "newer_than:2d" in terms
    assert terms.count("is:unread") == 1
    assert terms.count("in:inbox") == 1


def test_max_emails_is_clamped_to_1_through_500() -> None:
    assert validate_max_emails(1) == 1
    assert validate_max_emails(500) == 500
    for rejected in (0, -1, 501, 10_000):
        with pytest.raises(ValueError, match="max_emails"):
            validate_max_emails(rejected)


def test_api_rejects_out_of_range_max_emails(compat_session) -> None:
    async def scenario() -> None:
        async with compat_session() as s:
            for rejected in (0, 501):
                response = await s.post_run(f"clamp-{rejected}", max_emails=rejected)
                assert response.status_code == 422
            assert len(s.app.state.run_repository.runs) == 0

    asyncio.run(scenario())
