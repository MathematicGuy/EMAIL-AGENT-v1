"""FastAPI transport for report artifacts.

Every handler names a report through :class:`ReportFilename` and reaches the
folder only through the report store on the composed runtime (ADR-013). Neither
the path nor the naming rule appears here.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from cowork_agent.composition import runtime
from cowork_agent.domain.report_artifacts import (
    InvalidReportFilename,
    ReportArtifact,
    ReportArtifactStore,
    ReportFilename,
    ReportPdfRenderer,
    StoredReport,
)
from cowork_agent.persistence.report_artifacts import reveal_directory

logger = logging.getLogger(__name__)

_DOWNLOAD_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".csv": "text/csv",
    ".html": "text/html",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class SaveReportRequest(BaseModel):
    filename: str
    content: str


def _store(request: Request) -> ReportArtifactStore:
    return runtime(request).reports


def _renderer(request: Request) -> ReportPdfRenderer | None:
    return cast(
        ReportPdfRenderer | None, getattr(request.app.state, "report_pdf_renderer", None)
    )


def _filename(raw: str) -> ReportFilename:
    try:
        return ReportFilename.parse(raw)
    except InvalidReportFilename as exc:
        raise HTTPException(status_code=400, detail="Invalid filename") from exc


def _report_response(report: StoredReport) -> dict[str, Any]:
    return {
        "filename": report.filename.value,
        "content": report.content,
        "size": report.size,
        "updated_at": report.updated_at.isoformat(),
    }


async def _require_report(request: Request, raw: str) -> StoredReport:
    report = await _store(request).read(_filename(raw))
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def create_report_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

    @router.get("")
    async def list_reports(request: Request) -> list[dict[str, Any]]:
        return [_report_response(report) for report in await _store(request).list_reports()]

    @router.post("")
    async def save_report(body: SaveReportRequest, request: Request) -> dict[str, Any]:
        artifact = ReportArtifact(filename=_filename(body.filename), content=body.content)
        return _report_response(await _store(request).save(artifact))

    @router.post("/open-folder")
    async def open_reports_folder(request: Request) -> dict[str, Any]:
        location = _store(request).location
        try:
            reveal_directory(location)
        except OSError as exc:
            logger.warning("Failed to open reports folder %s: %s", location, exc)
            raise HTTPException(
                status_code=500, detail=f"Không thể mở thư mục: {exc}"
            ) from exc
        return {"status": "success", "path": str(location.resolve())}

    @router.get("/{filename}/download")
    async def download_report(filename: str, request: Request) -> FileResponse:
        name = _filename(filename)
        path = _store(request).path_for(name)
        if path is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return FileResponse(
            path=path,
            media_type=_DOWNLOAD_MEDIA_TYPES.get(name.suffix, "application/octet-stream"),
            filename=name.value,
            content_disposition_type="attachment",
        )

    @router.get("/{filename}/pdf")
    async def download_report_pdf(filename: str, request: Request) -> Any:
        report = await _require_report(request, filename)
        renderer = _renderer(request)
        if renderer is None:
            # A typed absence, not a 404: the report exists and the route is
            # mounted; this deployment has no PDF renderer registered. The
            # artifacts view falls back to downloading the source document.
            raise HTTPException(status_code=501, detail="pdf_export_unavailable")

        pdf = renderer.render(report)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{report.filename.value.removesuffix(".md")}.pdf"'
                )
            },
        )

    @router.delete("/{filename}")
    async def delete_report(filename: str, request: Request) -> dict[str, str]:
        name = _filename(filename)
        await _store(request).delete(name)
        return {"status": "success", "message": f"Deleted {name.value}"}

    return router


__all__ = ["SaveReportRequest", "create_report_router"]
