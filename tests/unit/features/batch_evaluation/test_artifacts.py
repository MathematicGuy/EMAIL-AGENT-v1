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


def test_manifest_rejects_private_and_path_shaped_keys(tmp_path: Path) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    for bad_payload in (
        {"summary": {"question": "private"}},
        {"apiKey": "secret-value"},
        {"summary": {"path": "private-value"}},
        {"summary": {"file": "private-value"}},
        {"summary": {"privateDetails": "private-value"}},
    ):
        with pytest.raises(UnsafeArtifact):
            store.write_manifest("job-1", bad_payload)


def test_artifact_store_rejects_non_finite_json_values(tmp_path: Path) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            store.write_manifest("job-1", {"summary": {"score": value}})
        with pytest.raises(ValueError):
            store.write_private_details("job-1", "detail-1", {"trace": value})

    # Reader rejection of handcrafted non-finite JSON
    manifest_ref = store.write_manifest("job-1", {"summary": {"succeeded": 1}})
    (tmp_path / manifest_ref).write_text('{"summary":{"score":NaN}}', encoding="utf-8")
    with pytest.raises(UnsafeArtifact, match="cannot be read"):
        store.read_manifest(manifest_ref)


def test_artifact_store_lifecycle_references_and_atomic_replace(tmp_path: Path) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)

    manifest_ref = store.write_manifest("job-1", {"summary": {"succeeded": 1}})
    private_ref = store.write_private_details("job-1", "detail-1", {"reply": "private"})

    assert not Path(manifest_ref).is_absolute()
    assert not Path(private_ref).is_absolute()
    assert store.manifest_reference("job-1") == manifest_ref
    assert store.read_manifest(manifest_ref) == {"summary": {"succeeded": 1}}
    assert store.read_private_details(private_ref) == {"reply": "private"}
    assert (tmp_path / private_ref).is_relative_to(tmp_path / ".runtime")

    # Atomic replace
    store.write_manifest("job-1", {"summary": {"succeeded": 2}})
    assert json.loads((tmp_path / manifest_ref).read_text(encoding="utf-8")) == {
        "summary": {"succeeded": 2}
    }
    assert not list((tmp_path / "evaluation-artifacts" / "manifests").glob("*.tmp"))


def test_artifact_store_security_escapes_and_overflow(tmp_path: Path) -> None:
    store = FilesystemEvaluationArtifactStore(tmp_path)
    private_ref = store.write_private_details("job-1", "detail-1", {"reply": "private"})
    private_path = tmp_path / private_ref

    # Exponent overflow
    private_path.write_text('{"score":1e9999}', encoding="utf-8")
    with pytest.raises(UnsafeArtifact, match="cannot be read") as error:
        store.read_private_details(private_ref)
    assert "1e9999" not in str(error.value)

    # Symlink / junction escape
    if os.name == "nt":
        outside = tmp_path / "outside"
        outside.mkdir()
        manifests = tmp_path / "evaluation-artifacts" / "manifests"
        manifests.parent.mkdir(parents=True, exist_ok=True)
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(manifests), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode == 0:
            with pytest.raises(UnsafeArtifact):
                store.write_manifest("job-1", {"summary": {"succeeded": 1}})
