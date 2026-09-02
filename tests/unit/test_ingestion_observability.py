import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cowork_agent.observability import (
    log_project_document_timing,
    safe_provider_label,
)


def test_enabled_jsonl_sink_writes_the_metadata_only_event_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "timing.jsonl"
    monkeypatch.setenv("CHAT_INGESTION_TIMING_LOG", str(log_path))

    log_project_document_timing(
        logging.getLogger("test.ingestion"),
        stage="source_download",
        duration_ms=12,
        outcome="success",
        document_id="document-1",
        timestamp=datetime(2026, 8, 18, 12, 34, 56, 789000, tzinfo=UTC),
        provider="supabase_private_storage",
    )

    assert json.loads(log_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "timestamp": "2026-08-18T12:34:56.789Z",
        "document_id": "document-1",
        "stage": "source_download",
        "duration_ms": 12,
        "outcome": "success",
        "provider": "supabase_private_storage",
    }


def test_provider_label_uses_only_the_adapter_type() -> None:
    class CredentialBearingAdapter:
        api_key = "must-not-be-logged"

    assert safe_provider_label(CredentialBearingAdapter()) == "credential_bearing_adapter"


def test_json_serialization_failure_is_non_throwing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_INGESTION_TIMING_LOG", "unused.jsonl")
    monkeypatch.setattr(
        "cowork_agent.observability.json.dumps",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("serialization failed")),
    )

    log_project_document_timing(
        logging.getLogger("test.ingestion"),
        stage="embedding",
        duration_ms=12,
        outcome="error",
        document_id="document-1",
    )
