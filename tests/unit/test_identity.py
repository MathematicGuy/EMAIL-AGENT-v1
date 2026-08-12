from cowork_agent.identity import VerifiedPrincipal


def test_verified_principal_exposes_workspace_as_the_authorization_scope() -> None:
    principal = VerifiedPrincipal(tenant_id="workspace-1", user_id="internal-user-1")

    assert principal.workspace_id == "workspace-1"
    assert principal.tenant_id == "workspace-1"
