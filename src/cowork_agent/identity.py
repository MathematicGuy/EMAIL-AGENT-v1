"""Verified Principal boundary for the local single-tenant MVP.

The Verified Principal is the authenticated tenant + user identity scoping
every operation. In the local MVP the user identity is the verified OAuth
email stored on the Mailbox Connection; the tenant is the fixed local tenant.
Caller-provided identifiers are never used for authorization decisions.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from cowork_agent.domain import MailboxConnection

LOCAL_TENANT_ID: str = "local"


class ConnectionNotOwnedError(LookupError):
    """The Verified Principal does not own the given Mailbox Connection."""


class OpaqueSessionResolver(Protocol):
    async def resolve(
        self, token: str, *, now: datetime
    ) -> "VerifiedPrincipal | None": ...


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    """Authenticated user identity scoping every operation."""

    user_id: str = "default_user"


def principal_for_connection(connection: MailboxConnection) -> VerifiedPrincipal:
    """Derive the Verified Principal from the connection's verified OAuth identity."""
    return VerifiedPrincipal(user_id=connection.email_address)


def ensure_principal_owns_connection(
    principal: VerifiedPrincipal, connection: MailboxConnection
) -> None:
    """Central ownership guard; every ownership decision goes through this function."""
    if principal.user_id != connection.user_id:
        raise ConnectionNotOwnedError(connection.id)


async def principal_from_opaque_session(
    token: str | None,
    sessions: OpaqueSessionResolver,
    *,
    now: datetime | None = None,
) -> VerifiedPrincipal | None:
    """Resolve an opaque cookie token; absent tokens are unauthenticated."""
    if not token:
        return None
    return await sessions.resolve(token, now=now or datetime.now(UTC))
