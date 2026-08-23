"""Atomic storage that separates public evaluation metadata from private details."""

from __future__ import annotations

import json
import math
import os
import re
import stat
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
        "path",
        "file",
        "directory",
        "root",
        "private",
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
        self._root = root.resolve()
        self._manifest_root = self._root / "evaluation-artifacts" / "manifests"
        self._private_root = self._root / ".runtime" / "evaluation-artifacts" / "private"

    def write_manifest(self, job_id: str, metadata: Mapping[str, object]) -> str:
        """Atomically persist only safe, response-ready metadata for a job."""

        _require_identifier(job_id, "job_id")
        _validate_public_metadata(metadata)
        path = self._contained_path(self._manifest_root / f"{job_id}.json")
        _write_atomically(path, _json_text(metadata))
        return self._reference(path)

    def write_private_details(self, job_id: str, artifact_id: str, details: object) -> str:
        """Atomically persist private details under the ignored runtime root."""

        _require_identifier(job_id, "job_id")
        _require_identifier(artifact_id, "artifact_id")
        path = self._contained_path(self._private_root / job_id / f"{artifact_id}.json")
        _write_atomically(path, _json_text(details))
        return self._reference(path)

    def manifest_reference(self, job_id: str) -> str:
        """Return the stable public reference for one validated job identifier."""

        _require_identifier(job_id, "job_id")
        return self._reference(self._contained_path(self._manifest_root / f"{job_id}.json"))

    def read_manifest(self, reference: str) -> Mapping[str, object]:
        """Read one public manifest through its opaque relative reference."""

        value = self._read(reference, self._manifest_root)
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise UnsafeArtifact("manifest must contain a JSON object")
        _validate_public_metadata(value)
        return value

    def read_private_details(self, reference: str) -> object:
        """Read private artifact details through a validated private reference."""

        return self._read(reference, self._private_root)

    def _reference(self, path: Path) -> str:
        return self._contained_path(path).relative_to(self._root).as_posix()

    def _contained_path(self, path: Path) -> Path:
        absolute = path.absolute()
        try:
            relative = absolute.relative_to(self._root)
        except ValueError as error:
            raise UnsafeArtifact("artifact path escapes its configured root") from error
        current = self._root
        for part in relative.parts:
            current /= part
            if _is_reparse_point(current):
                raise UnsafeArtifact("artifact path cannot traverse a symbolic link")
        resolved = path.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as error:
            raise UnsafeArtifact("artifact path escapes its configured root") from error
        return resolved

    def _read(self, reference: str, expected_root: Path) -> object:
        path = _path_from_reference(reference)
        candidate = self._contained_path(self._root / path)
        expected = self._contained_path(expected_root)
        try:
            candidate.relative_to(expected)
        except ValueError as error:
            raise UnsafeArtifact("artifact reference is outside its expected root") from error
        if candidate.suffix != ".json":
            raise UnsafeArtifact("artifact reference must name a JSON file")
        try:
            return json.loads(
                candidate.read_text(encoding="utf-8"),
                parse_constant=_reject_non_finite_json_constant,
                parse_float=_finite_json_float,
            )
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise UnsafeArtifact("artifact reference cannot be read") from error


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
    if isinstance(value, float) and not math.isfinite(value):
        raise UnsafeArtifact("artifact metadata cannot contain non-finite numbers")
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


def _path_from_reference(reference: str) -> Path:
    if not isinstance(reference, str) or not reference:
        raise UnsafeArtifact("artifact reference must be a non-empty relative path")
    path = Path(reference)
    if path.is_absolute() or path.drive or any(part == ".." for part in path.parts):
        raise UnsafeArtifact("artifact reference must be relative")
    return path


def _is_reparse_point(path: Path) -> bool:
    if not os.path.lexists(path):
        return False
    if path.is_symlink():
        return True
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _json_text(value: object) -> str:
    try:
        return (
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
            + "\n"
        )
    except (TypeError, ValueError) as error:
        raise UnsafeArtifact("artifact values must be JSON-compatible") from error


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is not allowed")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number is not allowed")
    return parsed


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
