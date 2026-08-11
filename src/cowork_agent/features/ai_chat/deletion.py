"""Metadata-only confirmation for an exact-scope AI Chat memory deletion."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryDeletionReport:
    session_cleared: bool
    profile_deleted: bool
    episodic_deleted_count: int
    complete: bool

    def __post_init__(self) -> None:
        if self.episodic_deleted_count < 0:
            raise ValueError("episodic_deleted_count must be nonnegative")
        if not self.complete:
            raise ValueError("deletion reports are emitted only after completion")
