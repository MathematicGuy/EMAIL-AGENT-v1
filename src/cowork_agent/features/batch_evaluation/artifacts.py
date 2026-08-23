"""Atomic storage that separates public evaluation metadata from private details."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path


class UnsafeArtifact(ValueError):
    """Raised when metadata could disclose private evaluation data or a secret."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_PRIVATE_KEY_PARTS = frozenset(
    {
        "authorization",
        "content",
        "credential",
        "dataset",
        "error",
        "message",
        "password",
        "prompt",
        "question",
        "reply",
        "secret",
        "token",
        "traceback",
    }
)
_PRIVATE_KEY_COMPACTS = frozenset({"apikey", "accesstoken"})


class FilesystemEvaluationArtifactStore:
    """Write public manifests and private details without returning local paths."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._manifest_root = root / "evaluation-artifacts" / "manifests"
        self._private_root = root / ".runtime" / "evaluation-artifacts" / "private"

    def write_manifest(self, job_id: str, metadata: Mapping[str, object]) -> str:
        """Atomically persist only safe, response-ready metadata for a job."""

        _require_identifier(job_id, "job_id")
        _validate_public_metadata(metadata)
        path = self._manifest_root / f"{job_id}.json"
        _write_atomically(path, _json_text(metadata))
        return self._reference(path)

    def write_private_details(self, job_id: str, artifact_id: str, details: object) -> str:
        """Atomically persist private details under the ignored runtime root."""

        _require_identifier(job_id, "job_id")
        _require_identifier(artifact_id, "artifact_id")
        path = self._private_root / job_id / f"{artifact_id}.json"
        _write_atomically(path, _json_text(details))
        return self._reference(path)

    def _reference(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()


def _validate_public_metadata(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or _is_private_key(key):
                raise UnsafeArtifact("artifact metadata contains a private key")
            _validate_public_metadata(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _validate_public_metadata(nested)
        return
    if isinstance(value, Path) or (isinstance(value, str) and Path(value).is_absolute()):
        raise UnsafeArtifact("artifact metadata cannot contain absolute paths")
    if value is None or isinstance(value, str | int | float | bool):
        return
    raise UnsafeArtifact("artifact metadata must be JSON-compatible")


def _is_private_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    compact = normalized.replace("_", "")
    return any(marker in compact for marker in _PRIVATE_KEY_COMPACTS) or bool(
        frozenset(part for part in normalized.split("_") if part) & _PRIVATE_KEY_PARTS
    )


def _require_identifier(value: str, name: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise UnsafeArtifact(f"{name} must be a safe identifier")


def _json_text(value: object) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    except (TypeError, ValueError) as error:
        raise UnsafeArtifact("artifact values must be JSON-compatible") from error


def _write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
