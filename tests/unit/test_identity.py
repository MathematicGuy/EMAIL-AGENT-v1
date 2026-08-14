import asyncio

from cowork_agent.identity import VerifiedPrincipal, principal_from_opaque_session


def test_verified_principal_exposes_workspace_for_persistence_scoping() -> None:
    principal = VerifiedPrincipal(tenant_id="workspace_123", user_id="user_123")

    assert principal.user_id == "user_123"
    assert principal.tenant_id == "workspace_123"
    assert principal.workspace_id == "workspace_123"


def test_opaque_session_resolver_returns_none_without_a_cookie() -> None:
    assert asyncio.run(principal_from_opaque_session(None, object(), now=None)) is None
