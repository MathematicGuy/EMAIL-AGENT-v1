"""Public API for the Cowork email agent."""

from .features.email_action_plan import CreateDigestRun, DigestWorker, GetDigestResult

__all__ = ["CreateDigestRun", "DigestWorker", "GetDigestResult"]
