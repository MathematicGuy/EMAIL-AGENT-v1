"""Raw process-document endpoints, including the OnlyOffice save callback.

These build their own corpus under ``tmp_path`` rather than asserting against the
tracked ``data/raw`` fixtures: the endpoints' behaviour is the subject, and coupling
to whichever documents happen to be checked in makes the suite fail on curation and
tempts tests into writing into a tracked directory.
"""

from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import cowork_agent.app as app_module
from cowork_agent.app import create_app
from cowork_agent.config import OnlyOfficeSettings
from cowork_agent.integrations.onlyoffice import jwt as onlyoffice_jwt
from cowork_agent.persistence.repositories.sqlite_raw_documents import (
    SQLiteRawDocumentRepository,
)

JWT_SECRET = "test-onlyoffice-secret"
DOC_SERVER = "http://docserver.test:8080"


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

    monkeypatch.setattr(app_module, "RAW_DOCS_DIR", raw_dir)
    monkeypatch.setattr(app_module, "EXTRACTED_DIR", extracted_dir)
    return raw_dir


async def _app_with_repo(tmp_path: Path, *, jwt_secret: str | None = JWT_SECRET):
    app = create_app()
    repo = SQLiteRawDocumentRepository(tmp_path / "raw_docs.db")
    await repo.initialize()
    app.state.raw_document_repository = repo
    app.state.onlyoffice_settings = OnlyOfficeSettings.from_env(
        {
            "ONLYOFFICE_SERVER_URL": DOC_SERVER,
            **({"ONLYOFFICE_JWT_SECRET": jwt_secret} if jwt_secret else {}),
        },
        load_env_file=False,
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
async def test_directory_traversal_cannot_escape_the_corpus(
    corpus: Path, tmp_path: Path
) -> None:
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
async def test_manifest_entry_escaping_the_corpus_is_ignored(
    corpus: Path, tmp_path: Path
) -> None:
    (tmp_path / "outside.md").write_text("secret outside the corpus", encoding="utf-8")
    (corpus.parent / "extracted" / "ingestion-manifest.json").write_text(
        '{"procedure.pdf": {"output": "../outside.md"}}', encoding="utf-8"
    )

    async with _client(create_app()) as client:
        res = await client.get("/api/v1/raw-documents/procedure.pdf/extracted")

    assert res.status_code == 404
    assert b"secret outside the corpus" not in res.content


@pytest.mark.asyncio
async def test_onlyoffice_config_is_signed_when_a_secret_is_set(
    corpus: Path, tmp_path: Path
) -> None:
    app, _ = await _app_with_repo(tmp_path)

    async with _client(app) as client:
        res = await client.get("/api/v1/raw-documents/decree.docx/onlyoffice-config")

    assert res.status_code == 200
    config = res.json()
    assert config["document"]["fileType"] == "docx"
    assert config["document"]["title"] == "decree.docx"
    assert config["document"]["key"]
    assert config["documentType"] == "word"
    assert config["documentServerUrl"] == DOC_SERVER
    assert config["editorConfig"]["callbackUrl"].endswith(
        "/api/v1/raw-documents/decree.docx/onlyoffice-callback"
    )

    claims = onlyoffice_jwt.decode(config["token"], JWT_SECRET)
    assert claims["document"]["key"] == config["document"]["key"]


@pytest.mark.asyncio
async def test_onlyoffice_config_without_a_secret_carries_no_token(
    corpus: Path, tmp_path: Path
) -> None:
    app, _ = await _app_with_repo(tmp_path, jwt_secret=None)

    async with _client(app) as client:
        res = await client.get("/api/v1/raw-documents/decree.docx/onlyoffice-config")

    assert res.status_code == 200
    assert "token" not in res.json()


@pytest.mark.asyncio
async def test_onlyoffice_config_works_without_lifespan_startup(corpus: Path) -> None:
    """The lazy repository fallback must create its schema, not blow up on first query."""
    app = create_app()
    assert getattr(app.state, "raw_document_repository", None) is None

    async with _client(app) as client:
        res = await client.get("/api/v1/raw-documents/decree.docx/onlyoffice-config")

    assert res.status_code == 200
    assert res.json()["document"]["key"]


def _mock_download(monkeypatch: pytest.MonkeyPatch, body: bytes) -> list[str]:
    """Replace httpx's GET and record every URL the callback tried to fetch."""
    attempted: list[str] = []

    class _Response:
        status_code = 200
        content = body

    async def _get(self, url, **kwargs):  # noqa: ANN001, ANN202
        attempted.append(str(url))
        return _Response()

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    return attempted


@pytest.mark.asyncio
async def test_onlyoffice_callback_saves_a_signed_request(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, repo = await _app_with_repo(tmp_path)
    target = corpus / "decree.docx"
    attempted = _mock_download(monkeypatch, b"edited docx body")

    payload = {"status": 2, "key": "k", "url": f"{DOC_SERVER}/cache/files/edited.docx"}
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={**payload, "token": onlyoffice_jwt.encode(payload, JWT_SECRET)},
        )

    assert res.status_code == 200
    assert res.json() == {"error": 0}
    assert attempted == [f"{DOC_SERVER}/cache/files/edited.docx"]
    assert target.read_bytes() == b"edited docx body"
    metadata = await repo.get("decree.docx")
    assert metadata is not None
    assert metadata.last_status == 2
    assert metadata.version == 1

    # OnlyOffice caches by document key, so each save must mint a fresh one or the
    # editor reopens the pre-edit copy.
    async with _client(app) as client:
        await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={**payload, "token": onlyoffice_jwt.encode(payload, JWT_SECRET)},
        )
    second = await repo.get("decree.docx")
    assert second is not None
    assert second.version == 2
    assert second.doc_key != metadata.doc_key


@pytest.mark.asyncio
async def test_onlyoffice_callback_ignores_non_save_statuses(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = await _app_with_repo(tmp_path)
    target = corpus / "decree.docx"
    attempted = _mock_download(monkeypatch, b"should not be written")

    payload = {"status": 4, "key": "k", "url": f"{DOC_SERVER}/cache/files/edited.docx"}
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={**payload, "token": onlyoffice_jwt.encode(payload, JWT_SECRET)},
        )

    assert res.status_code == 200
    assert res.json() == {"error": 0}
    assert attempted == []
    assert target.read_bytes() == b"fake docx body"


@pytest.mark.asyncio
async def test_onlyoffice_callback_rejects_an_unsigned_request(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = await _app_with_repo(tmp_path)
    target = corpus / "decree.docx"
    attempted = _mock_download(monkeypatch, b"attacker content")

    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={"status": 2, "url": f"{DOC_SERVER}/cache/files/edited.docx"},
        )

    assert res.status_code == 403
    assert attempted == []
    assert target.read_bytes() == b"fake docx body"


@pytest.mark.asyncio
async def test_onlyoffice_callback_rejects_a_forged_signature(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = await _app_with_repo(tmp_path)
    target = corpus / "decree.docx"
    attempted = _mock_download(monkeypatch, b"attacker content")

    payload = {"status": 2, "url": f"{DOC_SERVER}/cache/files/edited.docx"}
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={**payload, "token": onlyoffice_jwt.encode(payload, "wrong-secret")},
        )

    assert res.status_code == 403
    assert attempted == []
    assert target.read_bytes() == b"fake docx body"


@pytest.mark.asyncio
async def test_onlyoffice_callback_refuses_a_foreign_download_host(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correctly signed request still may not aim the fetch at an arbitrary host."""
    app, _ = await _app_with_repo(tmp_path)
    target = corpus / "decree.docx"
    attempted = _mock_download(monkeypatch, b"attacker content")

    payload = {"status": 2, "url": "http://169.254.169.254/latest/meta-data/"}
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={**payload, "token": onlyoffice_jwt.encode(payload, JWT_SECRET)},
        )

    assert res.status_code == 200
    assert res.json() == {"error": 1}
    assert attempted == []
    assert target.read_bytes() == b"fake docx body"


@pytest.mark.asyncio
async def test_onlyoffice_callback_refuses_a_non_http_download_url(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = await _app_with_repo(tmp_path)
    attempted = _mock_download(monkeypatch, b"attacker content")

    payload = {"status": 2, "url": "file:///etc/passwd"}
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={**payload, "token": onlyoffice_jwt.encode(payload, JWT_SECRET)},
        )

    assert res.json() == {"error": 1}
    assert attempted == []


@pytest.mark.asyncio
async def test_onlyoffice_callback_refuses_to_save_without_a_configured_secret(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No secret means no way to authenticate the caller, so writes are refused."""
    app, _ = await _app_with_repo(tmp_path, jwt_secret=None)
    target = corpus / "decree.docx"
    attempted = _mock_download(monkeypatch, b"attacker content")

    async with _client(app) as client:
        res = await client.post(
            "/api/v1/raw-documents/decree.docx/onlyoffice-callback",
            json={"status": 2, "url": f"{DOC_SERVER}/cache/files/edited.docx"},
        )

    assert res.status_code == 503
    assert attempted == []
    assert target.read_bytes() == b"fake docx body"
