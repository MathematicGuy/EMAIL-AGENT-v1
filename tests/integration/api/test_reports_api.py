"""Report artifact endpoints over the in-process ASGI transport.

The runtime is injected onto ``app.state`` (the ASGI transport never runs
``lifespan``) with a store pointing at a throwaway folder, which is the seam the
whole candidate exists to create: these cases never touch the repository's
tracked ``data/reports``.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from cowork_agent.app import create_app
from cowork_agent.composition import CoworkRuntime
from cowork_agent.persistence.report_artifacts import FileSystemReportArtifactStore


@pytest.fixture
def reports(tmp_path: Path):
    """An app whose composed runtime points at a throwaway report folder."""
    app = create_app()
    root = tmp_path / "reports"
    app.state.runtime = CoworkRuntime(reports=FileSystemReportArtifactStore(root))
    return app, root


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_is_empty_before_anything_is_saved(reports) -> None:
    app, _ = reports
    async with _client(app) as client:
        res = await client.get("/api/v1/reports")
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_save_then_list_round_trips_the_document(reports) -> None:
    app, root = reports
    async with _client(app) as client:
        saved = await client.post(
            "/api/v1/reports",
            json={"filename": "bao-cao-cccd.md", "content": "# Bao cao\n\nNoi dung"},
        )
        listed = await client.get("/api/v1/reports")

    assert saved.status_code == 200
    body = saved.json()
    assert body["filename"] == "bao-cao-cccd.md"
    assert body["content"] == "# Bao cao\n\nNoi dung"
    assert body["size"] > 0
    assert body["updated_at"]

    assert [item["filename"] for item in listed.json()] == ["bao-cao-cccd.md"]
    assert (root / "bao-cao-cccd.md").is_file()


@pytest.mark.asyncio
async def test_saving_a_traversing_filename_lands_inside_the_folder(reports) -> None:
    """The directory part is stripped, as it was before the store existed."""
    app, root = reports
    async with _client(app) as client:
        res = await client.post(
            "/api/v1/reports",
            json={"filename": "../../escaped.md", "content": "nope"},
        )

    assert res.status_code == 200
    assert res.json()["filename"] == "escaped.md"
    assert (root / "escaped.md").is_file()
    assert not (root.parent / "escaped.md").exists()
    assert not (root.parent.parent / "escaped.md").exists()


@pytest.mark.asyncio
async def test_saving_a_name_that_addresses_no_file_is_rejected(reports) -> None:
    app, root = reports
    async with _client(app) as client:
        res = await client.post("/api/v1/reports", json={"filename": "../..", "content": "nope"})

    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid filename"
    assert not root.exists()


@pytest.mark.asyncio
async def test_delete_removes_the_file_and_is_idempotent(reports) -> None:
    app, root = reports
    async with _client(app) as client:
        await client.post("/api/v1/reports", json={"filename": "x.md", "content": "x"})
        first = await client.delete("/api/v1/reports/x.md")
        second = await client.delete("/api/v1/reports/x.md")

    assert first.status_code == 200
    assert first.json()["status"] == "success"
    assert second.status_code == 200
    assert not (root / "x.md").exists()


@pytest.mark.asyncio
async def test_download_serves_the_stored_document_as_an_attachment(reports) -> None:
    app, _ = reports
    async with _client(app) as client:
        await client.post("/api/v1/reports", json={"filename": "bao-cao.md", "content": "noi dung"})
        res = await client.get("/api/v1/reports/bao-cao.md/download")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert "attachment" in res.headers["content-disposition"]
    assert res.text == "noi dung"


@pytest.mark.asyncio
async def test_download_of_an_unknown_report_is_404(reports) -> None:
    app, _ = reports
    async with _client(app) as client:
        res = await client.get("/api/v1/reports/missing.md/download")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_pdf_reports_its_own_absence_rather_than_404ing(reports) -> None:
    """No renderer is registered by default; the route says so in its own code."""
    app, _ = reports
    async with _client(app) as client:
        await client.post("/api/v1/reports", json={"filename": "bao-cao.md", "content": "noi dung"})
        res = await client.get("/api/v1/reports/bao-cao.md/pdf")

    assert res.status_code == 501
    assert res.json()["detail"] == "pdf_export_unavailable"


@pytest.mark.asyncio
async def test_pdf_uses_a_registered_renderer(reports) -> None:
    app, _ = reports

    class StubRenderer:
        def render(self, report, *, title=None) -> bytes:
            return b"%PDF-1.4 " + report.content.encode("utf-8")

    app.state.runtime = replace(
        app.state.runtime,
        report_pdf_renderer=StubRenderer(),
    )
    async with _client(app) as client:
        await client.post("/api/v1/reports", json={"filename": "bao-cao.md", "content": "noi dung"})
        res = await client.get("/api/v1/reports/bao-cao.md/pdf")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="bao-cao.pdf"' in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF-1.4")


@pytest.mark.asyncio
async def test_report_routes_are_mounted_once_with_unique_operation_ids() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    assert set(paths["/api/v1/reports"].keys()) == {"get", "post"}
    # The two routes the artifacts view was already calling against a 404.
    assert "get" in paths["/api/v1/reports/{filename}/download"]
    assert "get" in paths["/api/v1/reports/{filename}/pdf"]

    operation_ids = [
        operation["operationId"]
        for path in paths.values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "delete", "patch"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
