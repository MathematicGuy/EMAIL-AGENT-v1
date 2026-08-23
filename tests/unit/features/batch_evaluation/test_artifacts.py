import json
import math
import os
import subprocess
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


@pytest.mark.parametrize("key", ("path", "file", "directory", "root", "privateDetails"))
def test_manifest_rejects_path_and_private_shaped_keys_recursively(
    tmp_path: Path, key: str
) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    with pytest.raises(UnsafeArtifact):
        store.write_manifest("job-1", {"summary": {key: "private-value"}})


@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_artifact_store_rejects_non_finite_json_values(tmp_path: Path, value: float) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    with pytest.raises(ValueError):
        store.write_manifest("job-1", {"summary": {"score": value}})
    with pytest.raises(ValueError):
        store.write_private_details("job-1", "detail-1", {"trace": value})


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


def test_artifact_references_are_readable_without_exposing_paths(tmp_path: Path) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    manifest_ref = store.write_manifest("job-1", {"summary": {"succeeded": 1}})
    private_ref = store.write_private_details("job-1", "detail-1", {"reply": "private"})

    assert store.manifest_reference("job-1") == manifest_ref
    assert store.read_manifest(manifest_ref) == {"summary": {"succeeded": 1}}
    assert store.read_private_details(private_ref) == {"reply": "private"}


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_artifact_readers_reject_handcrafted_non_finite_json(
    tmp_path: Path, constant: str
) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)
    manifest_ref = store.write_manifest("job-1", {"summary": {"succeeded": 1}})
    private_ref = store.write_private_details("job-1", "detail-1", {"reply": "private"})
    (tmp_path / manifest_ref).write_text(
        '{"summary":{"score":' + constant + "}}", encoding="utf-8"
    )
    (tmp_path / private_ref).write_text(
        '{"trace":' + constant + "}", encoding="utf-8"
    )

    with pytest.raises(UnsafeArtifact, match="cannot be read"):
        store.read_manifest(manifest_ref)
    with pytest.raises(UnsafeArtifact, match="cannot be read"):
        store.read_private_details(private_ref)


def test_artifact_store_rejects_symlink_escapes(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction exercise")
    store = FilesystemEvaluationArtifactStore(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    manifests = tmp_path / "evaluation-artifacts" / "manifests"
    manifests.parent.mkdir()
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(manifests), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr

    with pytest.raises(UnsafeArtifact):
        store.write_manifest("job-1", {"summary": {"succeeded": 1}})
