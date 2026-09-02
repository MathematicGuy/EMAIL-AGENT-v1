import asyncio
from datetime import UTC, datetime

from cowork_agent.identity import (
    VerifiedPrincipal,
    create_guest_session,
    principal_from_opaque_session,
)


def test_verified_principal_exposes_workspace_for_persistence_scoping() -> None:
    principal = VerifiedPrincipal(tenant_id="workspace_123", user_id="user_123")

    assert principal.user_id == "user_123"
    assert principal.tenant_id == "workspace_123"
    assert principal.workspace_id == "workspace_123"


def test_opaque_session_resolver_returns_none_without_a_cookie() -> None:
    assert asyncio.run(principal_from_opaque_session(None, object(), now=None)) is None


def test_guest_session_uses_an_isolated_non_email_identity_and_opaque_token() -> None:
    class Identities:
        def __init__(self) -> None:
            self.identifiers: list[str] = []

        async def resolve_or_create_principal(self, identifier: str) -> VerifiedPrincipal:
            self.identifiers.append(identifier)
            return VerifiedPrincipal(tenant_id="guest-workspace", user_id="guest-user")

    class Sessions:
        def __init__(self) -> None:
            self.principals: list[VerifiedPrincipal] = []

        async def create(
            self, principal: VerifiedPrincipal, *, now: datetime, ttl_seconds: int
        ) -> tuple[str, datetime]:
            self.principals.append(principal)
            return "opaque-guest-token", now

    identities = Identities()
    sessions = Sessions()
    now = datetime(2026, 8, 17, tzinfo=UTC)

    principal, token = asyncio.run(
        create_guest_session(
            identities,
            sessions,
            now=now,
            ttl_seconds=3600,
            guest_id_factory=lambda: "browser-guest-1",
        )
    )

    assert identities.identifiers == ["guest-browser-guest-1@guest.invalid"]
    assert sessions.principals == [principal]
    assert token == "opaque-guest-token"
