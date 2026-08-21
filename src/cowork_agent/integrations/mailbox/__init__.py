"""Provider-neutral mailbox integration primitives."""

from .errors import (
    MailboxError,
    MailboxNotConnectedError,
    MailboxPermissionDeniedError,
    MailboxRateLimitedError,
    MailboxReauthRequiredError,
    MailboxTemporaryError,
)
from .router import ProviderRoutingMailboxAdapter

__all__ = [
    "MailboxError",
    "MailboxNotConnectedError",
    "MailboxPermissionDeniedError",
    "MailboxRateLimitedError",
    "MailboxReauthRequiredError",
    "MailboxTemporaryError",
    "ProviderRoutingMailboxAdapter",
]
