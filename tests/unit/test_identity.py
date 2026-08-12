import asyncio

from cowork_agent.identity import VerifiedPrincipal, principal_from_opaque_session


def test_verified_principal_exposes_workspace_as_the_authorization_scope() -> None:
    principal = VerifiedPrincipal(tenant_id="workspace-1", user_id="internal-user-1")

    assert principal.workspace_id == "workspace-1"
    assert principal.tenant_id == "workspace-1"


def test_opaque_session_resolver_returns_none_without_a_cookie() -> None:
    assert asyncio.run(principal_from_opaque_session(None, object(), now=None)) is None
