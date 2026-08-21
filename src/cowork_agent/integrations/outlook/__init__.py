"""Microsoft OAuth and read-only Graph mailbox integration."""

from .provider import (
    MICROSOFT_DEFAULT_SCOPES,
    MICROSOFT_MAIL_READ_SCOPE,
    MicrosoftOAuthDriver,
    OutlookConnectionService,
    OutlookMailboxAdapter,
    OutlookOAuthGrant,
)

__all__ = [
    "MICROSOFT_DEFAULT_SCOPES",
    "MICROSOFT_MAIL_READ_SCOPE",
    "MicrosoftOAuthDriver",
    "OutlookConnectionService",
    "OutlookMailboxAdapter",
    "OutlookOAuthGrant",
]
