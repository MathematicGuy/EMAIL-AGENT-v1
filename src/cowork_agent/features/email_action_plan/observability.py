"""Telemetry and development tracing (V1-M4 T4.4/T4.5).

T4.4 — basic telemetry (PRD-v1 FR-16): metadata-only :class:`TraceEvent`s
(§6.8) recording run status, route/reason codes, classifier confidence,
retrieval status/count, validation violation CODES, stage latency, and
error/fallback markers. Email content never enters this module.

T4.5 — development trace (PRD-v1 FR-15): an explicitly labeled, encrypted,
TTL-bounded full-content trace that cannot be enabled when ``APP_ENV`` is
production.
"""

import json
import logging
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cowork_agent.domain import RunStatus
from cowork_agent.domain.target_contracts import (
    TraceEvent,
    TraceLatency,
    TraceStatus,
)
from cowork_agent.integrations.gmail.auth import TokenCipher

from .ports import CompletionOutboxPort

logger = logging.getLogger(__name__)

#: FR-15 mandated label — present in every development trace record.
DEV_TRACE_MARKER = "ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE"

_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})


class TraceSink(Protocol):
    """Destination for metadata-only §6.8 trace events (FR-16)."""

    def record(self, event: TraceEvent) -> None: ...


class LoggingTraceSink:
    """Emits each trace event as one structured log line (metadata only)."""

    def __init__(self, sink_logger: logging.Logger | None = None) -> None:
        self._logger = sink_logger or logger

    def record(self, event: TraceEvent) -> None:
        self._logger.info(
            "trace %s", json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        )


class InMemoryTraceSink:
    """Deterministic test sink."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)


_LIFECYCLE_TRACE_STATUS = {
    RunStatus.SUCCEEDED: TraceStatus.SUCCESS,
    RunStatus.PARTIAL: TraceStatus.PARTIAL,
    RunStatus.FAILED: TraceStatus.FAILED,
}


class LifecycleEventPublisher:
    """Relays durable completion events to observers (V1-H T5.5).

    The outbox is the durable record; publication is at-least-once — the
    outbox row is idempotent per run, but relays can duplicate (crash
    between record and mark, or concurrent workers relaying before either
    marks). Harmless for logging sinks; revisit before a counting sink.
    """

    EVENT_NAME = "digest_run_completed"

    def __init__(self, outbox: CompletionOutboxPort, trace_sink: TraceSink | None) -> None:
        self._outbox = outbox
        self._trace_sink = trace_sink

    async def publish_pending(self) -> int:
        published = 0
        for event in await self._outbox.pending():
            if self._trace_sink is not None:
                try:
                    trace_status = _LIFECYCLE_TRACE_STATUS[event.status]
                except KeyError as exc:
                    raise ValueError(
                        f"Lifecycle event for run {event.run_id} carries"
                        f" non-terminal status {event.status!r}"
                    ) from exc
                self._trace_sink.record(
                    TraceEvent(
                        run_id=event.run_id,
                        user_id=event.user_id,
                        gmail_message_id=None,
                        event_name=self.EVENT_NAME,
                        status=trace_status,
                        route=None,
                        reason_codes=(),
                        classifier_confidence=None,
                        rag_result_count=None,
                        retrieval_status=None,
                        generation_status=None,
                        validation_status=None,
                        latency_ms=TraceLatency(),
                    )
                )
            await self._outbox.mark_published(event.run_id)
            published += 1
        return published


def is_production_env(environ: Mapping[str, str] | None = None) -> bool:
    mapping = os.environ if environ is None else environ
    return mapping.get("APP_ENV", "development").strip().lower() in _PRODUCTION_ENVIRONMENTS


class ProductionTraceForbiddenError(RuntimeError):
    """Raised when the development trace is requested in production."""


class EncryptedDevTraceSink:
    """FR-15 development trace: full-content records, encrypted at rest.

    Controls: hard production guard (constructor AND write path), explicit
    opt-in flag, the mandated marker on every record, automatic TTL expiry
    honored at read time, and a never-index/never-consolidate contract —
    records are append-only ciphertext with no consumer besides the
    developer reading them back.
    """

    def __init__(
        self,
        path: Path,
        encryption_key: str,
        *,
        enabled: bool,
        ttl_seconds: int,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if is_production_env(environ):
            raise ProductionTraceForbiddenError(
                "Development trace cannot be enabled in production (APP_ENV)."
            )
        self._enabled = enabled
        self._path = path
        self._cipher = TokenCipher(encryption_key)
        self._ttl_seconds = ttl_seconds

    @property
    def enabled(self) -> bool:
        return self._enabled

    def write(self, *, run_id: str, kind: str, payload: object) -> None:
        """Append one encrypted, markered, TTL-bounded trace record."""
        if is_production_env():
            raise ProductionTraceForbiddenError(
                "Development trace cannot be enabled in production (APP_ENV)."
            )
        if not self._enabled:
            return
        record = {
            "marker": DEV_TRACE_MARKER,
            "run_id": run_id,
            "kind": kind,
            "recorded_at": datetime.now(UTC).isoformat(),
            "expires_at": datetime.fromtimestamp(
                time.time() + self._ttl_seconds, tz=UTC
            ).isoformat(),
            "payload": payload,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = self._cipher.encrypt(json.dumps(record, ensure_ascii=False, default=str))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self) -> list[dict[str, object]]:
        """Decrypt and return every unexpired record (restricted developer access).

        TTL bounds read access; expired ciphertext stays on disk until the
        file is deleted (production retention/purge arrives with V1-H).
        """
        if not self._path.exists():
            return []
        now = datetime.now(UTC)
        records: list[dict[str, object]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(self._cipher.decrypt(line))
            if datetime.fromisoformat(str(record["expires_at"])) > now:
                records.append(record)
        return records


def dev_trace_sink_from_env(
    data_dir: Path,
    encryption_key: str,
    environ: Mapping[str, str] | None = None,
) -> EncryptedDevTraceSink | None:
    """Best-effort construction: production or opt-out yields ``None``.

    Never raises at app startup: an invalid key or APP_ENV=production only
    means the development trace stays off.
    """
    mapping = os.environ if environ is None else environ
    enabled = mapping.get("DEV_TRACE_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return None
    try:
        return EncryptedDevTraceSink(
            data_dir / "dev_trace.jsonl.enc",
            encryption_key,
            enabled=True,
            ttl_seconds=int(mapping.get("DEV_TRACE_TTL_SECONDS", "86400")),
            environ=mapping,
        )
    except (ProductionTraceForbiddenError, ValueError) as exc:
        logger.warning("Development trace disabled: %s", exc)
        return None


def emit_security_scan_trace(
    trace_sink: TraceSink | None,
    *,
    run_id: str,
    user_id: str,
    urls_scanned_count: int,
    attachments_scanned_count: int,
    threats_detected_count: int,
    quarantined_count: int,
    highest_threat_level: str,
    latency_ms: int = 0,
    degraded: bool = False,
) -> None:
    """Emit metadata-only trace event for email security scanning (§6.8 / Task 3.4)."""
    if trace_sink is None:
        return

    reason_codes = (
        f"URLS:{urls_scanned_count}",
        f"ATTACHMENTS:{attachments_scanned_count}",
        f"THREATS:{threats_detected_count}",
        f"QUARANTINED:{quarantined_count}",
        f"HIGHEST:{highest_threat_level}",
    )
    status = (
        TraceStatus.SUCCESS
        if quarantined_count == 0 and not degraded
        else TraceStatus.PARTIAL
    )
    validation_status = "SECURITY_QUARANTINE" if quarantined_count > 0 else "SECURITY_CLEAN"
    generation_status = "SECURITY_SCAN_DEGRADED" if degraded else None

    trace_sink.record(
        TraceEvent(
            run_id=run_id,
            user_id=user_id,
            gmail_message_id=None,
            event_name="email_security_scan",
            status=status,
            route=None,
            reason_codes=reason_codes,
            classifier_confidence=None,
            rag_result_count=None,
            retrieval_status=None,
            generation_status=generation_status,
            validation_status=validation_status,
            latency_ms=TraceLatency(email=latency_ms),
        )
    )

