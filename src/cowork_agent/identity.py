"""Verified Principal boundary for the local single-tenant MVP.

The Verified Principal is the authenticated tenant + user identity scoping
every operation. In the local MVP the user identity is the verified OAuth
email stored on the Mailbox Connection; the tenant is the fixed local tenant.
Caller-provided identifiers are never used for authorization decisions.
"""

from dataclasses import dataclass

from cowork_agent.domain import MailboxConnection

LOCAL_TENANT_ID: str = "local"


class ConnectionNotOwnedError(LookupError):
    """The Verified Principal does not own the given Mailbox Connection."""


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    """Authenticated workspace + user identity scoping every operation."""

    tenant_id: str
    user_id: str

    @property
    def workspace_id(self) -> str:
        """Workspace authorization scope; ``tenant_id`` remains a transition alias."""
        return self.tenant_id


def principal_for_connection(connection: MailboxConnection) -> VerifiedPrincipal:
    """Derive the Verified Principal from the connection's verified OAuth identity."""
    return VerifiedPrincipal(tenant_id=LOCAL_TENANT_ID, user_id=connection.email_address)


def ensure_principal_owns_connection(
    principal: VerifiedPrincipal, connection: MailboxConnection
) -> None:
    """Central ownership guard; every ownership decision goes through this function."""
    if principal.user_id != connection.email_address:
        raise ConnectionNotOwnedError(connection.id)
