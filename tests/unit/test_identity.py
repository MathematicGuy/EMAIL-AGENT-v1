import asyncio

from cowork_agent.identity import VerifiedPrincipal, principal_from_opaque_session


def test_verified_principal_has_user_id_and_no_tenant_id() -> None:
    principal = VerifiedPrincipal(user_id="user_123")

    assert principal.user_id == "user_123"
    assert not hasattr(principal, "tenant_id")


def test_opaque_session_resolver_returns_none_without_a_cookie() -> None:
    assert asyncio.run(principal_from_opaque_session(None, object(), now=None)) is None
