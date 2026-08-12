"""In-process Short-Term Memory store for ephemeral Run state.

This is the local in-process adapter for Short-Term Memory (target
architecture §Memory System, box 1): it holds Ephemeral Envelopes — including
raw email bodies — only until the Run finalizer clears them or the safety TTL
expires. Entries are never persisted or logged.
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ShortTermEntry:
    envelopes: tuple[EphemeralEmailEnvelope, ...]
    expires_at: datetime


class ShortTermStore:
    """Safety-TTL store for transient raw-email state of a single Run."""

    def __init__(
        self,
        ttl_seconds: int = 1800,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._entries: dict[str, ShortTermEntry] = {}
        self._lock = threading.Lock()

    def put(self, run_id: str, envelopes: Sequence[EphemeralEmailEnvelope]) -> None:
        """Store a tuple copy of the Run's Ephemeral Envelopes under a safety TTL."""
        with self._lock:
            self._sweep_locked()
            self._entries[run_id] = ShortTermEntry(
                envelopes=tuple(envelopes),
                expires_at=self._clock() + self._ttl,
            )

    def get(self, run_id: str) -> tuple[EphemeralEmailEnvelope, ...] | None:
        """Return the Run's envelopes, or None when absent or expired (and evicted)."""
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[run_id]
                return None
            return entry.envelopes

    def clear(self, run_id: str) -> None:
        """Run finalizer: drop the entry; idempotent and safe for unknown ids."""
        with self._lock:
            self._entries.pop(run_id, None)

    def sweep(self) -> int:
        """Delete every entry whose safety TTL has passed; return the count removed."""
        with self._lock:
            return self._sweep_locked()

    def _sweep_locked(self) -> int:
        now = self._clock()
        expired = [
            entry_run_id
            for entry_run_id, entry in self._entries.items()
            if entry.expires_at <= now
        ]
        for entry_run_id in expired:
            del self._entries[entry_run_id]
        return len(expired)
