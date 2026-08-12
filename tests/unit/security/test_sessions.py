from datetime import UTC, datetime, timedelta

from cowork_agent.security.sessions import (
    new_session_token,
    session_expiry,
    session_token_hash,
)


def test_session_tokens_are_random_and_only_their_hash_is_persistable() -> None:
    first = new_session_token()
    second = new_session_token()

    assert first != second
    assert session_token_hash(first) == session_token_hash(first)
    assert session_token_hash(first) != first
    assert len(session_token_hash(first)) == 64


def test_session_expiry_is_derived_from_the_configured_ttl() -> None:
    now = datetime(2026, 8, 12, 9, tzinfo=UTC)

    assert session_expiry(now, 3600) == now + timedelta(hours=1)
