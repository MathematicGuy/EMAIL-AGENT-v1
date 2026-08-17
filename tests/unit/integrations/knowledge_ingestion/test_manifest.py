from __future__ import annotations

import json
from pathlib import Path

import pytest

from cowork_agent.integrations.knowledge_ingestion.manifest import (
    ManifestStore,
    sha256_file,
    write_markdown_atomically,
)
from cowork_agent.integrations.knowledge_ingestion.models import ManifestEntry


def test_manifest_skips_only_successful_unchanged_source(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")

    store.record(ManifestEntry(source="a.pdf", sha256="abc", status="succeeded", output="a.md"))

    assert store.should_skip("a.pdf", "abc") is True
    assert store.should_skip("a.pdf", "different") is False


@pytest.mark.parametrize("invalid_content", ["", "not valid json"])
def test_manifest_treats_empty_or_invalid_content_as_empty(
    tmp_path: Path, invalid_content: str
) -> None:
    manifest_path = tmp_path / "manifest.json"
    store = ManifestStore(manifest_path)
    manifest_path.write_text(invalid_content, encoding="utf-8")

    assert store.load() == {}
    assert store.should_skip("a.pdf", "abc") is False


def test_manifest_does_not_skip_failed_entry(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    store.record(ManifestEntry(source="failed.pdf", sha256="abc", status="failed", output=""))

    assert store.should_skip("failed.pdf", "abc") is False


def test_manifest_persists_only_safe_entry_fields(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    entry = ManifestEntry(
        source="a.pdf",
        sha256="abc",
        status="succeeded",
        output="a.md",
        extractor="pymupdf",
        page_count=2,
        processed_at="2026-08-10T00:00:00+00:00",
        reason_code=None,
    )

    store.record(entry)

    assert store.load() == {"a.pdf": entry}
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")) == {
        "a.pdf": {
            "extractor": "pymupdf",
            "output": "a.md",
            "page_count": 2,
            "processed_at": "2026-08-10T00:00:00+00:00",
            "reason_code": None,
            "sha256": "abc",
            "source": "a.pdf",
            "status": "succeeded",
            "title": "",
            "document_date": "",
        }
    }


def test_manifest_persists_and_reloads_nonempty_title(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    entry = ManifestEntry(
        source="a.pdf",
        sha256="abc",
        status="succeeded",
        output="a.md",
        title="Company Policy",
    )

    store.record(entry)

    assert store.load() == {"a.pdf": entry}
    assert store.load()["a.pdf"].title == "Company Policy"
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["a.pdf"][
        "title"
    ] == "Company Policy"
    assert store.should_skip("a.pdf", "abc") is True


def test_manifest_loads_missing_title_as_empty(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "a.pdf": {
                    "extractor": "",
                    "output": "a.md",
                    "page_count": 0,
                    "processed_at": "",
                    "reason_code": None,
                    "sha256": "abc",
                    "source": "a.pdf",
                    "status": "succeeded",
                }
            }
        ),
        encoding="utf-8",
    )
    store = ManifestStore(manifest_path)

    loaded = store.load()["a.pdf"]
    assert loaded.title == ""
    assert store.should_skip("a.pdf", "abc") is True


def test_manifest_persists_and_reloads_nonempty_document_date(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    entry = ManifestEntry(
        source="a.pdf",
        sha256="abc",
        status="succeeded",
        output="a.md",
        document_date="2026-08-07",
    )

    store.record(entry)

    assert store.load() == {"a.pdf": entry}
    assert store.load()["a.pdf"].document_date == "2026-08-07"
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["a.pdf"][
        "document_date"
    ] == "2026-08-07"


def test_manifest_missing_document_date_key_loads_as_empty(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "a.pdf": {
                    "extractor": "",
                    "output": "a.md",
                    "page_count": 0,
                    "processed_at": "",
                    "reason_code": None,
                    "sha256": "abc",
                    "source": "a.pdf",
                    "status": "succeeded",
                }
            }
        ),
        encoding="utf-8",
    )
    store = ManifestStore(manifest_path)

    loaded = store.load()["a.pdf"]
    assert loaded.document_date == ""
    assert store.should_skip("a.pdf", "abc") is True


def test_manifest_treats_non_string_document_date_as_empty(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "a.pdf": {
                    "extractor": "",
                    "output": "a.md",
                    "page_count": 0,
                    "processed_at": "",
                    "reason_code": None,
                    "sha256": "abc",
                    "source": "a.pdf",
                    "status": "succeeded",
                    "document_date": 20260807,
                }
            }
        ),
        encoding="utf-8",
    )
    store = ManifestStore(manifest_path)

    loaded = store.load()["a.pdf"]
    assert loaded.document_date == ""
    assert store.should_skip("a.pdf", "abc") is True


def test_manifest_treats_non_string_title_as_empty(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "a.pdf": {
                    "extractor": "",
                    "output": "a.md",
                    "page_count": 0,
                    "processed_at": "",
                    "reason_code": None,
                    "sha256": "abc",
                    "source": "a.pdf",
                    "status": "succeeded",
                    "title": 123,
                }
            }
        ),
        encoding="utf-8",
    )
    store = ManifestStore(manifest_path)

    loaded = store.load()["a.pdf"]
    assert loaded.title == ""
    assert store.should_skip("a.pdf", "abc") is True


def test_sha256_file_reads_large_files(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"a" * (64 * 1024) + b"b")

    assert sha256_file(source) == "935bf57d7f52181f095c3a3484b68e542037e287f7cde4ffe8a32896d428a1b1"


def test_empty_markdown_does_not_replace_existing(tmp_path: Path) -> None:
    destination = tmp_path / "policy.md"
    destination.write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty"):
        write_markdown_atomically(destination, " \n")

    assert destination.read_text(encoding="utf-8") == "# Existing\n"


def test_markdown_write_replaces_existing_content(tmp_path: Path) -> None:
    destination = tmp_path / "policy.md"
    destination.write_text("# Existing\n", encoding="utf-8")

    write_markdown_atomically(destination, "# Replacement\n")

    assert destination.read_text(encoding="utf-8") == "# Replacement\n"
