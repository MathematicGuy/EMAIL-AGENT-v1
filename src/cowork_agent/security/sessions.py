"""Opaque application session-token primitives."""

import hashlib
import secrets
from datetime import datetime, timedelta


def new_session_token() -> str:
    """Create a high-entropy token sent once in an HttpOnly cookie."""
    return secrets.token_urlsafe(48)


def session_token_hash(token: str) -> str:
    """Return the stable database representation of a session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry(now: datetime, ttl_seconds: int) -> datetime:
    """Derive a session expiry from a validated positive TTL."""
    return now + timedelta(seconds=ttl_seconds)
