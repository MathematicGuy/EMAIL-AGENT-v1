import asyncio
import json
import logging
from pathlib import Path

import numpy as np
import pytest

from cowork_agent.integrations.rag.project_index import TurbovecProjectIndexStore


class FakeIndex:
    def write(self, path: str) -> None:
        Path(path).write_bytes(b"index")


class FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, Path]] = []

    async def upload_file(self, object_key: str, source: Path) -> None:
        self.uploads.append((object_key, source))


def test_add_logs_local_update_write_and_snapshot_upload_without_sensitive_data(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        store = object.__new__(TurbovecProjectIndexStore)
        store._root = tmp_path
        store._storage = storage
        store._vector_size = 2
        store._bit_width = 4
        store._locks = {}
        store._normalize = lambda vectors: np.asarray(vectors, dtype=np.float32)
        store._open_or_create = lambda project_id: FakeIndex()
        store._add_replacing = lambda index, matrix, ids: None
        timing_path = tmp_path / "timing.jsonl"
        monkeypatch.setenv("CHAT_INGESTION_TIMING_LOG", str(timing_path))

        with caplog.at_level(logging.INFO):
            await store.add(
                project_id="project-secret",
                document_id="document-1",
                vector_ids=[11],
                vectors=[[1.0, 0.0]],
            )

        timing = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("project_document_ingestion_timing ")
        ]
        assert [message.split()[1] for message in timing] == [
            "stage=local_index_update",
            "stage=snapshot_upload",
        ]
        assert all(" document_id=document-1" in message for message in timing)
        assert all(" duration_ms=" in message for message in timing)
        assert all(" outcome=success" in message for message in timing)
        assert all("project_id=" not in message for message in timing)
        assert "project-secret" not in "\n".join(timing)
        assert storage.uploads == [
            ("project-indexes/project-secret.tvim", tmp_path / "project-secret.tvim")
        ]
        events = [json.loads(line) for line in timing_path.read_text().splitlines()]
        assert [event["stage"] for event in events] == [
            "local_index_update",
            "snapshot_upload",
        ]
        assert [event["snapshot_bytes"] for event in events] == [5, 5]
        assert events[0]["provider"] == "turbovec_project_index_store"
        assert events[1]["provider"] == "fake_storage"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_stage", ["local_index_update", "snapshot_upload"])
def test_add_logs_error_for_the_failed_reached_stage(
    failure_stage: str,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    class FailingIndex(FakeIndex):
        def write(self, path: str) -> None:
            del path
            raise OSError("local path must-not-be-logged")

    class FailingStorage(FakeStorage):
        async def upload_file(self, object_key: str, source: Path) -> None:
            del object_key, source
            raise OSError("signed-url=must-not-be-logged")

    async def scenario() -> None:
        store = object.__new__(TurbovecProjectIndexStore)
        store._root = tmp_path
        store._storage = FailingStorage() if failure_stage == "snapshot_upload" else FakeStorage()
        store._vector_size = 2
        store._bit_width = 4
        store._locks = {}
        store._normalize = lambda vectors: np.asarray(vectors, dtype=np.float32)
        store._open_or_create = lambda project_id: (
            FailingIndex() if failure_stage == "local_index_update" else FakeIndex()
        )
        store._add_replacing = lambda index, matrix, ids: None

        with caplog.at_level(logging.INFO), pytest.raises(OSError):
            await store.add(
                project_id="project-secret",
                document_id="document-1",
                vector_ids=[11],
                vectors=[[1.0, 0.0]],
            )

        timing = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("project_document_ingestion_timing ")
        ]
        expected_stages = (
            ["stage=local_index_update"]
            if failure_stage == "local_index_update"
            else ["stage=local_index_update", "stage=snapshot_upload"]
        )
        assert [message.split()[1] for message in timing] == expected_stages
        assert " outcome=error" in timing[-1]
        assert all(" outcome=success" in message for message in timing[:-1])
        assert all(
            sensitive not in "\n".join(timing)
            for sensitive in ("project-secret", "local path", "signed-url")
        )

    asyncio.run(scenario())
