"""Safe, atomic persistence for knowledge-ingestion progress."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .models import ManifestEntry

_HASH_BLOCK_SIZE = 64 * 1024
_ENTRY_FIELDS = (
    "source",
    "sha256",
    "status",
    "output",
    "extractor",
    "page_count",
    "processed_at",
    "reason_code",
    "title",
)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(_HASH_BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def write_markdown_atomically(path: Path, markdown: str) -> None:
    """Replace a Markdown file only after its non-empty content is durable."""
    if not markdown.strip():
        msg = "Markdown content must be non-empty"
        raise ValueError(msg)
    _write_atomically(path, markdown)


class ManifestStore:
    """Store safe ingestion metadata keyed by the relative source path."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, ManifestEntry]:
        """Load valid manifest entries, treating absent or malformed data as empty."""
        try:
            raw_manifest = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw_manifest, dict):
            return {}

        entries: dict[str, ManifestEntry] = {}
        for source, raw_entry in raw_manifest.items():
            entry = _entry_from_json(source, raw_entry)
            if entry is not None:
                entries[source] = entry
        return entries

    def should_skip(self, source: str, sha256: str) -> bool:
        """Return whether a matching source was previously processed successfully."""
        entry = self.load().get(source)
        return entry is not None and entry.status == "succeeded" and entry.sha256 == sha256

    def record(self, entry: ManifestEntry) -> None:
        """Persist an entry without retaining unapproved metadata fields."""
        entries = self.load()
        entries[entry.source] = entry
        payload = {source: _entry_to_json(value) for source, value in entries.items()}
        _write_atomically(self._path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _entry_from_json(source: object, raw_entry: object) -> ManifestEntry | None:
    if not isinstance(source, str) or not isinstance(raw_entry, dict):
        return None
    source_value = raw_entry.get("source")
    sha256 = raw_entry.get("sha256")
    status = raw_entry.get("status")
    output = raw_entry.get("output")
    extractor = raw_entry.get("extractor")
    page_count = raw_entry.get("page_count")
    processed_at = raw_entry.get("processed_at")
    reason_code = raw_entry.get("reason_code")
    title = raw_entry.get("title")
    if (
        source_value != source
        or not isinstance(source_value, str)
        or not isinstance(sha256, str)
        or not isinstance(status, str)
        or not isinstance(output, str)
        or not isinstance(extractor, str)
        or not isinstance(processed_at, str)
    ):
        return None
    if not isinstance(page_count, int) or isinstance(page_count, bool):
        return None
    if reason_code is not None and not isinstance(reason_code, str):
        return None
    if not isinstance(title, str):
        title = ""
    return ManifestEntry(
        source=source_value,
        sha256=sha256,
        status=status,
        output=output,
        extractor=extractor,
        page_count=page_count,
        processed_at=processed_at,
        reason_code=reason_code,
        title=title,
    )


def _entry_to_json(entry: ManifestEntry) -> dict[str, object]:
    return {field: getattr(entry, field) for field in _ENTRY_FIELDS}


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
