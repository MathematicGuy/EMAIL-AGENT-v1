import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from cowork_agent.app import create_app

pytestmark = pytest.mark.extended


@pytest.mark.asyncio
async def test_list_raw_documents_endpoint():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/raw-documents")
        assert res.status_code == 200
        docs = res.json()
        assert isinstance(docs, list)
        assert len(docs) >= 15

        # Check structure of items
        for doc in docs:
            assert "filename" in doc
            assert "file_type" in doc
            assert "size" in doc
            assert "updated_at" in doc
            assert "has_extracted_md" in doc


@pytest.mark.asyncio
async def test_get_raw_document_pdf_inline():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/raw-documents/cap_lai_cccd.pdf")
        assert res.status_code == 200
        assert "application/pdf" in res.headers.get("content-type", "")
        assert "inline" in res.headers.get("content-disposition", "")
        assert len(res.content) > 0


@pytest.mark.asyncio
async def test_get_raw_document_extracted_markdown():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/raw-documents/cap_lai_cccd.pdf/extracted")
        assert res.status_code == 200
        data = res.json()
        assert data["filename"] == "cap_lai_cccd.pdf"
        assert "content" in data
        assert len(data["content"]) > 0


@pytest.mark.asyncio
async def test_get_raw_document_not_found_and_traversal():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/v1/raw-documents/non_existent_file.pdf")
        assert res.status_code == 404

        res_traversal = await client.get("/api/v1/raw-documents/..%2Fsecrets.txt")
        assert res_traversal.status_code in (400, 404)


@pytest.mark.asyncio
async def test_get_onlyoffice_config(tmp_path):
    app = create_app()
    from cowork_agent.persistence.repositories.sqlite_raw_documents import (
        SQLiteRawDocumentRepository,
    )

    repo = SQLiteRawDocumentRepository(tmp_path / "raw_docs.db")
    await repo.initialize()
    app.state.raw_document_repository = repo

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get(
            "/api/v1/raw-documents/01_2021_ND-CP_283247.docx/onlyoffice-config"
        )
        assert res.status_code == 200
        config = res.json()
        assert "document" in config
        assert config["document"]["fileType"] == "docx"
        assert config["document"]["title"] == "01_2021_ND-CP_283247.docx"
        assert "key" in config["document"]
        assert len(config["document"]["key"]) > 0
        assert "url" in config["document"]
        assert "editorConfig" in config
        assert "callbackUrl" in config["editorConfig"]
        assert "documentServerUrl" in config


@pytest.mark.asyncio
async def test_post_onlyoffice_callback_save(tmp_path, monkeypatch):
    app = create_app()
    from cowork_agent.persistence.repositories.sqlite_raw_documents import (
        SQLiteRawDocumentRepository,
    )

    repo = SQLiteRawDocumentRepository(tmp_path / "raw_docs.db")
    await repo.initialize()
    app.state.raw_document_repository = repo

    # Create dummy raw doc file for testing
    from cowork_agent.app import RAW_DOCS_DIR

    test_file = RAW_DOCS_DIR / "_test_dummy_doc.docx"
    test_file.write_bytes(b"initial doc content")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Test callback with status 4 (no changes)
            res_no_change = await client.post(
                "/api/v1/raw-documents/_test_dummy_doc.docx/onlyoffice-callback",
                json={"status": 4, "key": "some_key"},
            )
            assert res_no_change.status_code == 200
            assert res_no_change.json() == {"error": 0}

            # Test callback with status 2 (ready for save)
            # Simulate external download URL
            class MockResponse:
                status_code = 200
                content = b"modified doc binary from onlyoffice"

            async def mock_get(self, url, **kwargs):
                return MockResponse()

            monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

            res_save = await client.post(
                "/api/v1/raw-documents/_test_dummy_doc.docx/onlyoffice-callback",
                json={
                    "status": 2,
                    "key": "test_key",
                    "url": "http://fake-onlyoffice/download/doc.docx",
                },
            )
            assert res_save.status_code == 200
            assert res_save.json() == {"error": 0}
            assert test_file.read_bytes() == b"modified doc binary from onlyoffice"

            # Check SQLite metadata updated
            meta = await repo.get("_test_dummy_doc.docx")
            assert meta is not None
            assert meta.last_status == 2
            assert meta.version >= 1
    finally:
        if test_file.exists():
            test_file.unlink()

