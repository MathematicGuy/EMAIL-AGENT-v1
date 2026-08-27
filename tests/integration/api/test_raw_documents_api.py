"""Raw process-document endpoints: listing, serving, upload, save and delete.

These build their own corpus under ``tmp_path`` rather than asserting against the
tracked ``data/raw`` fixtures: the endpoints' behaviour is the subject, and coupling
to whichever documents happen to be checked in makes the suite fail on curation and
tempts tests into writing into a tracked directory.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

import cowork_agent.api.knowledge as knowledge_api
from cowork_agent.app import create_app
from cowork_agent.composition import CoworkRuntime
from cowork_agent.persistence.repositories.sqlite_raw_documents import (
    SQLiteRawDocumentRepository,
)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the endpoints at a throwaway data/raw + data/extracted pair."""
    raw_dir = tmp_path / "raw"
    extracted_dir = tmp_path / "extracted"
    raw_dir.mkdir()
    extracted_dir.mkdir()

    (raw_dir / "procedure.pdf").write_bytes(b"%PDF-1.4 fake pdf body")
    (raw_dir / "decree.docx").write_bytes(b"fake docx body")
    (raw_dir / "notes.txt").write_text("plain notes", encoding="utf-8")
    (extracted_dir / "procedure.md").write_text("# Procedure\nextracted body", encoding="utf-8")
    (extracted_dir / "notes.md").write_text("# Notes\nextracted notes", encoding="utf-8")
    (extracted_dir / "ingestion-manifest.json").write_text(
        '{"procedure.pdf": {"output": "procedure.md"}, "notes.txt": {"output": "notes.md"}}',
        encoding="utf-8",
    )

    # The corpus locations moved to the knowledge router with the handlers
    # that read them (slice 03-1); patch them where they are now defined.
    monkeypatch.setattr(knowledge_api, "RAW_DOCS_DIR", raw_dir)
    monkeypatch.setattr(knowledge_api, "EXTRACTED_DIR", extracted_dir)
    return raw_dir


async def _app_with_repo(tmp_path: Path):
    """An app whose composed control plane owns a throwaway metadata store.

    The write endpoints read the repository off the runtime like every other
    group (ADR-013); the ``app.state.raw_document_repository`` memo they used
    to fall back on is gone, so a test that writes has to compose one.
    """
    app = create_app()
    repo = SQLiteRawDocumentRepository(tmp_path / "raw_docs.db")
    await repo.initialize()
    app.state.runtime = CoworkRuntime(
        reports=None,  # type: ignore[arg-type]
        control_plane=cast(Any, SimpleNamespace(raw_document_repository=repo)),
    )
    return app, repo


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_raw_documents_reports_extraction_status(corpus: Path) -> None:
    async with _client(create_app()) as client:
        res = await client.get("/api/v1/raw-documents")

    assert res.status_code == 200
    by_name = {doc["filename"]: doc for doc in res.json()}
    assert set(by_name) == {"procedure.pdf", "decree.docx", "notes.txt"}
    assert by_name["procedure.pdf"]["file_type"] == "pdf"
    assert by_name["procedure.pdf"]["has_extracted_md"] is True
    assert by_name["procedure.pdf"]["extracted_md_name"] == "procedure.md"
    assert by_name["decree.docx"]["has_extracted_md"] is False
    assert by_name["procedure.pdf"]["size"] > 0
    assert by_name["procedure.pdf"]["updated_at"]


@pytest.mark.asyncio
async def test_get_raw_document_serves_pdf_inline(corpus: Path) -> None:
    async with _client(create_app()) as client:
        res = await client.get("/api/v1/raw-documents/procedure.pdf")

    assert res.status_code == 200
    assert "application/pdf" in res.headers.get("content-type", "")
    assert "inline" in res.headers.get("content-disposition", "")
    assert res.content == b"%PDF-1.4 fake pdf body"


@pytest.mark.asyncio
async def test_get_extracted_markdown(corpus: Path) -> None:
    async with _client(create_app()) as client:
        res = await client.get("/api/v1/raw-documents/procedure.pdf/extracted")

    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "procedure.pdf"
    assert body["extracted_md_name"] == "procedure.md"
    assert "extracted body" in body["content"]


@pytest.mark.asyncio
async def test_get_extracted_markdown_missing_is_404(corpus: Path) -> None:
    async with _client(create_app()) as client:
        res = await client.get("/api/v1/raw-documents/decree.docx/extracted")

    assert res.status_code == 404


@pytest.mark.asyncio
async def test_unknown_document_is_404(corpus: Path) -> None:
    async with _client(create_app()) as client:
        res = await client.get("/api/v1/raw-documents/nope.pdf")

    assert res.status_code == 404


@pytest.mark.asyncio
async def test_directory_traversal_cannot_escape_the_corpus(corpus: Path, tmp_path: Path) -> None:
    (tmp_path / "secrets.txt").write_text("do not serve me", encoding="utf-8")

    async with _client(create_app()) as client:
        encoded = await client.get("/api/v1/raw-documents/..%2Fsecrets.txt")
        dotted = await client.get("/api/v1/raw-documents/..")

    assert encoded.status_code in (400, 404)
    assert dotted.status_code in (400, 404)
    # The point of the assertion is that the sibling file never leaves the box.
    assert b"do not serve me" not in encoded.content
    assert b"do not serve me" not in dotted.content


@pytest.mark.asyncio
async def test_manifest_entry_escaping_the_corpus_is_ignored(corpus: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.md").write_text("secret outside the corpus", encoding="utf-8")
    (corpus.parent / "extracted" / "ingestion-manifest.json").write_text(
        '{"procedure.pdf": {"output": "../outside.md"}}', encoding="utf-8"
    )
    # Drop the legitimate match so only the escaping manifest entry could answer:
    # otherwise the stem-matching fallback serves procedure.md and the assertion
    # passes without ever exercising the containment check.
    (corpus.parent / "extracted" / "procedure.md").unlink()

    async with _client(create_app()) as client:
        res = await client.get("/api/v1/raw-documents/procedure.pdf/extracted")

    assert res.status_code == 404
    assert b"secret outside the corpus" not in res.content


# --- Endpoints added on main: upload, direct save, delete -------------------
# Ported onto the `corpus` fixture. The originals wrote `_test_*.docx` into the
# tracked data/raw and data/extracted directories and removed them in a finally
# block, so an interrupted run left stray files in the committed corpus.


@pytest.mark.asyncio
async def test_upload_stores_the_file_and_records_metadata(corpus: Path, tmp_path: Path) -> None:
    app, repo = await _app_with_repo(tmp_path)
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/upload",
            files={"file": ("uploaded.docx", b"uploaded docx body", "application/octet-stream")},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "uploaded"
    assert body["filename"] == "uploaded.docx"
    assert body["file_type"] == "docx"
    assert (corpus / "uploaded.docx").read_bytes() == b"uploaded docx body"
    assert await repo.get("uploaded.docx") is not None


@pytest.mark.asyncio
async def test_upload_rejects_an_unsupported_extension(corpus: Path, tmp_path: Path) -> None:
    app, _ = await _app_with_repo(tmp_path)
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/upload",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
        )

    assert res.status_code == 400
    assert not (corpus / "payload.exe").exists()


@pytest.mark.asyncio
async def test_upload_strips_directories_from_the_client_filename(
    corpus: Path, tmp_path: Path
) -> None:
    app, _ = await _app_with_repo(tmp_path)
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/upload",
            files={"file": ("../escaped.docx", b"body", "application/octet-stream")},
        )

    assert res.status_code == 200
    assert res.json()["filename"] == "escaped.docx"
    assert (corpus / "escaped.docx").is_file()
    assert not (corpus.parent / "escaped.docx").exists()


@pytest.mark.asyncio
async def test_put_overwrites_the_document_and_bumps_the_version(
    corpus: Path, tmp_path: Path
) -> None:
    app, repo = await _app_with_repo(tmp_path)
    async with _client(app) as client:
        res = await client.put(
            "/api/v1/raw-documents/decree.docx",
            content=b"replaced body",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert res.status_code == 200
    assert res.json()["status"] == "saved"
    assert (corpus / "decree.docx").read_bytes() == b"replaced body"
    metadata = await repo.get("decree.docx")
    assert metadata is not None and metadata.last_status == 2


@pytest.mark.asyncio
async def test_delete_removes_the_raw_file_and_its_extracted_markdown(
    corpus: Path, tmp_path: Path
) -> None:
    app, repo = await _app_with_repo(tmp_path)
    extracted = corpus.parent / "extracted" / "procedure.md"
    assert extracted.is_file()

    async with _client(app) as client:
        res = await client.delete("/api/v1/raw-documents/procedure.pdf")

    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
    assert not (corpus / "procedure.pdf").exists()
    assert not extracted.exists()
    # Regression: the delete path once reached the repository through an
    # unawaited coroutine behind a `hasattr(repo, "delete")` guard, so the
    # metadata row silently survived the delete.
    assert await repo.get("procedure.pdf") is None
