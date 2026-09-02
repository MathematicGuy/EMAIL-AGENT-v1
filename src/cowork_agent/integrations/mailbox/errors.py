"""Provider-neutral, publicly safe mailbox failures."""

from cowork_agent.features.email_action_plan.ports import (
    MailboxTemporaryError as PortMailboxTemporaryError,
)


class MailboxError(RuntimeError):
    error_code = "MAILBOX_ERROR"
    safe_message = "The mailbox could not be read."


class MailboxNotConnectedError(MailboxError, LookupError):
    error_code = "MAILBOX_NOT_CONNECTED"
    safe_message = "The mailbox is not connected."


class MailboxReauthRequiredError(MailboxError):
    error_code = "MAILBOX_REAUTH_REQUIRED"
    safe_message = "Mailbox access needs to be reconnected."


class MailboxPermissionDeniedError(MailboxError):
    error_code = "MAILBOX_PERMISSION_DENIED"
    safe_message = "The account has not granted read access to email."


class MailboxTemporaryError(PortMailboxTemporaryError, MailboxError):
    error_code = "MAILBOX_TEMPORARY_ERROR"
    safe_message = "The email service is temporarily unavailable."


class MailboxRateLimitedError(MailboxTemporaryError):
    error_code = "MAILBOX_RATE_LIMITED"
    safe_message = "The email service is temporarily rate limiting requests."
