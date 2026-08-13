"""Unit tests for the Artifact Creation, Preview & Download Flow API (ADR-008)."""

from pathlib import Path

from fastapi.testclient import TestClient

from cowork_agent.api.reports import LocalReportStorage, markdown_to_docx_bytes, sanitize_filename
from cowork_agent.app import create_app


def test_sanitize_filename() -> None:
    assert sanitize_filename("../../../secret.txt") == "secret.txt.md"
    assert sanitize_filename("Báo Cáo.md") == "Báo Cáo.md"
    assert sanitize_filename("") == "Báo cáo.md"


def test_markdown_to_docx_bytes() -> None:
    md = "# Title\n\n- Item 1\n- Item 2\n\nParagraph text"
    docx_bytes = markdown_to_docx_bytes(md)
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 0
    # ZIP file signature for DOCX
    assert docx_bytes.startswith(b"PK")


def test_reports_api_local_storage_flow(tmp_path: Path) -> None:
    app = create_app()
    report_dir = tmp_path / "workspace" / "reports"
    app.state.report_storage = LocalReportStorage(workspace_dir=report_dir)

    client = TestClient(app)

    # 1. List initially empty
    resp = client.get("/api/v1/reports")
    assert resp.status_code == 200
    assert resp.json() == []

    # 2. Create report
    create_resp = client.post(
        "/api/v1/reports",
        json={"filename": "test_report.md", "content": "# Test Report\n\nSample content"},
    )
    assert create_resp.status_code == 201
    created_data = create_resp.json()
    ref_id = created_data["ref_id"]
    assert "Test Report" in ref_id

    # 3. List contains newly created report
    list_resp = client.get("/api/v1/reports")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["ref_id"] == ref_id

    # 4. Fetch content preview
    content_resp = client.get(f"/api/v1/reports/{ref_id}")
    assert content_resp.status_code == 200
    assert content_resp.text == "# Test Report\n\nSample content"

    # 5. Download DOCX
    download_resp = client.get(f"/api/v1/reports/{ref_id}/download")
    assert download_resp.status_code == 200
    expected_content_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert download_resp.headers["content-type"] == expected_content_type
    assert download_resp.content.startswith(b"PK")

    # 6. Delete report
    del_resp = client.delete(f"/api/v1/reports/{ref_id}")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"success": True}

    # 7. Confirm deletion
    get_deleted = client.get(f"/api/v1/reports/{ref_id}")
    assert get_deleted.status_code == 404
