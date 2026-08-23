import json
from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.artifacts import (
    FilesystemEvaluationArtifactStore,
    UnsafeArtifact,
)


def test_manifest_rejects_private_metadata_recursively(tmp_path: Path) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    with pytest.raises(UnsafeArtifact):
        store.write_manifest("job-1", {"summary": {"question": "private"}})
    with pytest.raises(UnsafeArtifact):
        store.write_manifest("job-1", {"apiKey": "secret-value"})


def test_artifact_store_keeps_private_details_under_runtime_root_and_returns_relative_refs(
    tmp_path: Path,
) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    manifest_ref = store.write_manifest("job-1", {"summary": {"succeeded": 1}})
    private_ref = store.write_private_details("job-1", "detail-1", {"reply": "private"})

    assert not Path(manifest_ref).is_absolute()
    assert not Path(private_ref).is_absolute()
    assert (tmp_path / manifest_ref).read_text(encoding="utf-8") == (
        '{\n  "summary": {\n    "succeeded": 1\n  }\n}\n'
    )
    assert json.loads((tmp_path / private_ref).read_text(encoding="utf-8")) == {
        "reply": "private"
    }
    assert (tmp_path / private_ref).is_relative_to(tmp_path / ".runtime")


def test_manifest_write_replaces_existing_file_without_leaving_temporary_files(
    tmp_path: Path,
) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    reference = store.write_manifest("job-1", {"summary": {"succeeded": 1}})
    store.write_manifest("job-1", {"summary": {"succeeded": 2}})

    assert json.loads((tmp_path / reference).read_text(encoding="utf-8")) == {
        "summary": {"succeeded": 2}
    }
    assert not list((tmp_path / "evaluation-artifacts" / "manifests").glob("*.tmp"))
