"""Small, metadata-only observability helpers shared across application layers."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal

PROJECT_DOCUMENT_TIMING_PREFIX = "project_document_ingestion_timing"
ProjectDocumentTimingOutcome = Literal["success", "error"]
DatabaseHostClass = Literal["loopback", "remote"]
_JSONL_LOCK = threading.Lock()


def log_project_document_timing(
    logger: logging.Logger,
    *,
    stage: str,
    started: float | None = None,
    duration_ms: int | None = None,
    outcome: ProjectDocumentTimingOutcome,
    document_id: str | None = None,
    project_id: str | None = None,
    timestamp: datetime | None = None,
    database_host_class: DatabaseHostClass | None = None,
    snapshot_bytes: int | None = None,
    provider: str | None = None,
) -> None:
    """Emit one metadata-only project-document stage duration."""
    if duration_ms is None:
        if started is None:
            raise ValueError("project document timing requires a duration or start time")
        duration_ms = max(0, int((perf_counter() - started) * 1000))
    else:
        duration_ms = max(0, int(duration_ms))
    safe_provider = _safe_label(provider) if provider is not None else None
    suffix = ""
    if database_host_class is not None:
        suffix += f" database_host_class={database_host_class}"
    if snapshot_bytes is not None:
        suffix += f" snapshot_bytes={max(0, int(snapshot_bytes))}"
    if safe_provider is not None:
        suffix += f" provider={safe_provider}"
    if document_id is not None:
        logger.info(
            "%s stage=%s duration_ms=%d outcome=%s document_id=%s%s",
            PROJECT_DOCUMENT_TIMING_PREFIX,
            stage,
            duration_ms,
            outcome,
            document_id,
            suffix,
        )
        _write_jsonl_event(
            logger,
            timestamp=timestamp,
            document_id=document_id,
            stage=stage,
            duration_ms=duration_ms,
            outcome=outcome,
            database_host_class=database_host_class,
            snapshot_bytes=snapshot_bytes,
            provider=safe_provider,
        )
    elif project_id is not None:
        logger.info(
            "%s stage=%s duration_ms=%d outcome=%s project_id=%s%s",
            PROJECT_DOCUMENT_TIMING_PREFIX,
            stage,
            duration_ms,
            outcome,
            project_id,
            suffix,
        )


def safe_provider_label(adapter: object) -> str:
    """Return a credential-free provider label derived only from its type."""
    class_name = type(adapter).__name__
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
    return _safe_label(snake_case) or "unknown"


def database_host_class(connection: object) -> DatabaseHostClass:
    """Classify the address used by the active psycopg connection."""
    info = getattr(connection, "info", None)
    hostaddr = getattr(info, "hostaddr", None)
    host = str(hostaddr or getattr(info, "host", ""))
    if not host or host == "localhost" or host.startswith(("/", "\\")):
        return "loopback"
    try:
        return "loopback" if ipaddress.ip_address(host.split("%", 1)[0]).is_loopback else "remote"
    except ValueError:
        try:
            addresses = socket.getaddrinfo(host, None)
        except OSError:
            return "remote"
        resolved = {
            str(item[4][0]).split("%", 1)[0]
            for item in addresses
            if item[4] and item[4][0]
        }
        return (
            "loopback"
            if resolved and all(ipaddress.ip_address(address).is_loopback for address in resolved)
            else "remote"
        )


def _safe_label(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")[:80]


def _write_jsonl_event(
    logger: logging.Logger,
    *,
    timestamp: datetime | None,
    document_id: str,
    stage: str,
    duration_ms: int,
    outcome: ProjectDocumentTimingOutcome,
    database_host_class: DatabaseHostClass | None,
    snapshot_bytes: int | None,
    provider: str | None,
) -> None:
    try:
        _write_jsonl_event_unchecked(
            timestamp=timestamp,
            document_id=document_id,
            stage=stage,
            duration_ms=duration_ms,
            outcome=outcome,
            database_host_class=database_host_class,
            snapshot_bytes=snapshot_bytes,
            provider=provider,
        )
    except Exception:
        try:
            logger.warning("Project document timing sink write failed")
        except Exception:
            pass


def _write_jsonl_event_unchecked(
    *,
    timestamp: datetime | None,
    document_id: str,
    stage: str,
    duration_ms: int,
    outcome: ProjectDocumentTimingOutcome,
    database_host_class: DatabaseHostClass | None,
    snapshot_bytes: int | None,
    provider: str | None,
) -> None:
    destination = os.environ.get("CHAT_INGESTION_TIMING_LOG", "").strip()
    if not destination:
        return
    occurred_at = timestamp or datetime.now(UTC)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    payload: dict[str, object] = {
        "schema_version": 1,
        "timestamp": occurred_at.astimezone(UTC).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
        "document_id": document_id,
        "stage": stage,
        "duration_ms": duration_ms,
        "outcome": outcome,
    }
    if database_host_class is not None:
        payload["database_host_class"] = database_host_class
    if snapshot_bytes is not None:
        payload["snapshot_bytes"] = max(0, int(snapshot_bytes))
    if provider is not None:
        payload["provider"] = provider
    encoded = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _JSONL_LOCK:
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
