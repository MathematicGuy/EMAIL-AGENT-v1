import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from cowork_agent.persistence.repositories.projects import PostgresProjectRepository


class Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        del args


class Connection:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self._rows = rows
        # The configured hostname looks local, but libpq connected remotely.
        self.info = SimpleNamespace(host="localhost", hostaddr="203.0.113.8")

    def transaction(self) -> Transaction:
        return Transaction()

    async def execute(self, query: str, params: tuple[object, ...]) -> Cursor:
        del query, params
        return Cursor(self._rows.pop(0))


class ConnectionContext:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> Connection:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        del args


class Pool:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def connection(self) -> ConnectionContext:
        return ConnectionContext(self._connection)


def test_successful_claim_records_queue_delay_from_job_timestamps_and_active_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def scenario() -> None:
        created_at = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        claimed_at = created_at + timedelta(seconds=5)
        expires_at = created_at + timedelta(days=1)
        document_row: tuple[object, ...] = (
            "document-1",
            "project-1",
            "workspace-1",
            "user-1",
            "private.pdf",
            "application/pdf",
            10,
            "0" * 64,
            "private/source",
            "received",
            expires_at,
            None,
            None,
            None,
            None,
            None,
            created_at,
            created_at,
            created_at,
            claimed_at,
        )
        timing_path = tmp_path / "timing.jsonl"
        monkeypatch.setenv("CHAT_INGESTION_TIMING_LOG", str(timing_path))
        repository = PostgresProjectRepository(  # type: ignore[arg-type]
            Pool(Connection([document_row, ("document-1",)]))
        )

        claimed = await repository.claim_job("document-1")

        assert claimed is not None and claimed.status == "extracting"
        event = json.loads(timing_path.read_text(encoding="utf-8"))
        assert event == {
            "schema_version": 1,
            "timestamp": "2026-08-18T12:00:05.000Z",
            "document_id": "document-1",
            "stage": "queue_delay",
            "duration_ms": 5000,
            "outcome": "success",
            "database_host_class": "remote",
            "provider": "connection",
        }

    asyncio.run(scenario())
