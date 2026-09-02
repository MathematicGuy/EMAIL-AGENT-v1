"""Verified Principal boundary for the local single-tenant MVP.

The Verified Principal is the authenticated tenant + user identity scoping
every operation. In the local MVP the user identity is the verified OAuth
email stored on the Mailbox Connection; the tenant is the fixed local tenant.
Caller-provided identifiers are never used for authorization decisions.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from cowork_agent.domain import MailboxConnection

LOCAL_TENANT_ID: str = "local"


class ConnectionNotOwnedError(LookupError):
    """The Verified Principal does not own the given Mailbox Connection."""


class OpaqueSessionResolver(Protocol):
    async def resolve(self, token: str, *, now: datetime) -> "VerifiedPrincipal | None": ...


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    """Server-resolved user identity scoping every operation."""

    tenant_id: str = LOCAL_TENANT_ID
    user_id: str = "default_user"

    @property
    def workspace_id(self) -> str:
        """Postgres control-plane name for the verified tenant scope."""

        return self.tenant_id


class PrincipalRepository(Protocol):
    async def resolve_or_create_principal(self, identifier: str) -> VerifiedPrincipal: ...


class OpaqueSessionIssuer(Protocol):
    async def create(
        self,
        principal: VerifiedPrincipal,
        *,
        now: datetime,
        ttl_seconds: int,
    ) -> tuple[str, datetime]: ...


def _new_guest_id() -> str:
    return uuid4().hex


async def create_guest_session(
    identities: PrincipalRepository,
    sessions: OpaqueSessionIssuer,
    *,
    ttl_seconds: int,
    now: datetime | None = None,
    guest_id_factory: Callable[[], str] = _new_guest_id,
) -> tuple[VerifiedPrincipal, str]:
    """Create a browser-isolated guest principal and opaque session token."""
    guest_identifier = f"guest-{guest_id_factory()}@guest.invalid"
    principal = await identities.resolve_or_create_principal(guest_identifier)
    token, _ = await sessions.create(
        principal,
        now=now or datetime.now(UTC),
        ttl_seconds=ttl_seconds,
    )
    return principal, token


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
