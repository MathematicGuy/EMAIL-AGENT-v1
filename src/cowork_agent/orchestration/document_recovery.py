"""Lease recovery for project-document jobs claimed by a crashed worker."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

DEFAULT_DOCUMENT_LEASE_TIMEOUT = timedelta(minutes=15)


class ProjectDocumentLeaseRepository(Protocol):
    async def reset_stale_jobs(self, *, claimed_before: datetime) -> int: ...


async def recover_stale_document_jobs(
    repository: ProjectDocumentLeaseRepository,
    *,
    now: datetime,
    lease_timeout: timedelta = DEFAULT_DOCUMENT_LEASE_TIMEOUT,
) -> int:
    """Make an expired document lease available for the next polling worker."""
    return await repository.reset_stale_jobs(claimed_before=now - lease_timeout)
