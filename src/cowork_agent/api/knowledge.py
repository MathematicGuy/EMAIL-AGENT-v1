"""FastAPI transport for the knowledge corpus and its raw source documents.

Three path families land here because they are one subject: the document
pipeline. ``/v1/cowork/chat/document-health`` reports whether that pipeline can
run, ``/v1/mail-todo/knowledge/*`` reads what it has indexed, and
``/api/v1/raw-documents/*`` manages the source files it indexes from. The
router therefore declares no prefix and each handler carries its full path,
following ``api/evaluation_jobs.py``.

``RAW_DOCS_DIR`` and ``EXTRACTED_DIR`` are module-level so handlers and tests
agree on one location; do not shadow them with a local copy inside the factory.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from cowork_agent.composition import runtime
from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    SemanticRetrievalRequest,
)
from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort
from cowork_agent.integrations.rag.knowledge_base import KnowledgeDocument
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.persistence.repositories.sqlite_raw_documents import (
    SQLiteRawDocumentRepository,
)

from .dependencies import control_plane_required

logger = logging.getLogger(__name__)

RAW_DOCS_DIR = Path(__file__).resolve().parents[3] / "data" / "raw"
EXTRACTED_DIR = Path(__file__).resolve().parents[3] / "data" / "extracted"


class KnowledgeChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)



def _resolve_raw_document(filename: str) -> tuple[str, Path]:
    """Map a request path segment onto a real file directly inside ``RAW_DOCS_DIR``.

    ``Path(...).name`` strips any directory part, and the containment check then
    rejects a symlink inside the corpus that points somewhere else. Raises the HTTP
    error the four raw-document handlers all want, so they stay identical.
    """
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = RAW_DOCS_DIR / safe_name
    if not target.is_file() or not target.resolve().is_relative_to(RAW_DOCS_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Raw document not found")
    return safe_name, target



def _load_raw_manifest() -> dict[str, str]:
    manifest_file = EXTRACTED_DIR / "ingestion-manifest.json"
    if not manifest_file.is_file():
        return {}
    try:
        raw = json.loads(manifest_file.read_text(encoding="utf-8"))
        return {
            k: str(v["output"]) for k, v in raw.items() if isinstance(v, dict) and "output" in v
        }
    except Exception as exc:
        logger.warning("Failed to load ingestion-manifest.json: %s", exc)
        return {}


def _find_associated_extracted_docs(filename: str, manifest: dict[str, str]) -> list[Path]:
    """Find all extracted markdown files associated with a raw document."""
    if not EXTRACTED_DIR.exists():
        return []

    matches: list[Path] = []
    manifest_target = manifest.get(filename)
    if manifest_target:
        # The manifest is generated locally today, but its `output` values are
        # still data: a `../` or absolute entry would otherwise let /extracted
        # read -- and DELETE, via the delete endpoint -- files outside the
        # extracted corpus.
        p = (EXTRACTED_DIR / manifest_target).resolve()
        if p.is_file() and p.is_relative_to(EXTRACTED_DIR.resolve()):
            matches.append(p)

    raw_key = re.sub(r"[^a-z0-9]", "", Path(filename).stem.lower())
    if raw_key:
        for item in EXTRACTED_DIR.iterdir():
            if (
                item.is_file()
                and item.suffix.lower() == ".md"
                and item.name != "ingestion-manifest.json"
                and item not in matches
            ):
                if re.sub(r"[^a-z0-9]", "", item.stem.lower()) == raw_key:
                    matches.append(item)
    return matches


def _resolve_extracted_doc(filename: str, manifest: dict[str, str]) -> str | None:
    matches = _find_associated_extracted_docs(filename, manifest)
    return matches[0].name if matches else None


def _raw_document_repo(request: Request) -> SQLiteRawDocumentRepository:
    """The raw-document store the composed control plane owns.

    ADR-013 kept a self-heal ``app.state`` memo here while the routes still
    lived on ``create_app``: a no-lifespan test could reach a handler with no
    runtime assembled, so the helper built a repository under the process
    working directory and cached it on the app. That was the last untyped
    fallback, and it is gone -- the read is now the same typed read as every
    other group access, which also means the return type is concrete rather
    than ``Any``.
    """

    return control_plane_required(request).raw_document_repository


def create_knowledge_router() -> APIRouter:
    """Mount document health, knowledge reads and raw-document management."""

    router = APIRouter(tags=["knowledge"])

    @router.get("/v1/cowork/chat/document-health", response_model=None)
    async def document_health(request: Request) -> JSONResponse:
        # Every check reads one composed group through the runtime seam
        # (ADR-013); an absent group degrades to the disabled/unavailable
        # states the old missing-key reads produced.
        state_runtime = runtime(request)
        chat = state_runtime.chat
        mailbox = state_runtime.mailbox
        email_rag = state_runtime.email_rag
        control_plane = state_runtime.control_plane
        settings = chat.user_documents_settings if chat is not None else None
        redis_client = control_plane.redis_client if control_plane is not None else None
        checks: dict[str, str] = {
            "feature": "enabled" if settings is not None and settings.enabled else "disabled",
            "postgresql": "disabled",
            "supabase_storage": "disabled",
            "redis": "disabled",
            "redis_mode": "redis" if redis_client is not None else "local",
            "project_index": "disabled",
            "gemini_embeddings": "disabled",
            "ocr": "optional_unavailable",
            "classifier": "disabled",
            "worker_queue": "unavailable",
        }
        if settings is None or not settings.enabled:
            return JSONResponse({"status": "disabled", "checks": checks})
        if email_rag is not None and email_rag.document_embeddings_configured:
            checks["gemini_embeddings"] = "configured"
        checks["classifier"] = (
            "ready"
            if chat is not None and chat.chat_routing_service is not None
            else "unavailable"
        )
        pool = control_plane.pg_pool if control_plane is not None else None
        if pool is not None:
            try:
                async with pool.connection() as connection:
                    await connection.execute("SELECT 1")
                checks["postgresql"] = "ready"
            except Exception:
                checks["postgresql"] = "unavailable"
        checks["supabase_storage"] = (
            "configured"
            if mailbox is not None and mailbox.private_storage is not None
            else "unavailable"
        )
        if redis_client is not None:
            try:
                await redis_client.ping()  # type: ignore[attr-defined]
                checks["redis"] = "ready"
            except Exception:
                checks["redis"] = "unavailable"
        else:
            checks["redis"] = "local_fallback"
        index_store = email_rag.project_document_index if email_rag is not None else None
        if index_store is not None:
            # A .tvim is pulled per project on demand, so the only thing that
            # can be checked without a project in hand is that the API can
            # actually write the root it will cache snapshots into.
            try:
                index_store.root.mkdir(parents=True, exist_ok=True)
                checks["project_index"] = (
                    "ready" if os.access(index_store.root, os.W_OK) else ("unavailable")
                )
            except OSError:
                checks["project_index"] = "unavailable"
        project_repository = (
            control_plane.project_repository if control_plane is not None else None
        )
        if project_repository is not None:
            try:
                if await project_repository.worker_heartbeat_is_fresh(max_age_seconds=120):
                    checks["worker_queue"] = "ready"
            except Exception:
                checks["worker_queue"] = "unavailable"
        required = [
            "supabase_storage",
            "project_index",
            "gemini_embeddings",
            "classifier",
            "worker_queue",
        ]
        if pool is not None:
            required.append("postgresql")
        ready = all(checks[name] in {"ready", "configured"} for name in required)
        return JSONResponse(
            {"status": "ready" if ready else "degraded", "checks": checks},
            status_code=200 if ready else 503,
        )

    @router.get("/v1/mail-todo/knowledge/ready")
    async def knowledge_ready(request: Request) -> dict[str, Any]:
        email_rag = runtime(request).email_rag
        documents: tuple[KnowledgeDocument, ...] = (
            email_rag.knowledge_documents if email_rag is not None else ()
        )
        memory: SemanticMemoryPort = (
            email_rag.semantic_memory if email_rag is not None else NullSemanticMemory()
        )
        chunk_count = sum(len(doc.chunks) for doc in documents)
        is_null = type(memory).__name__ == "NullSemanticMemory"
        if not documents:
            status = "unavailable"
        elif is_null:
            status = "degraded"
        else:
            status = "ready"
        return {
            "status": status,
            "document_count": len(documents),
            "chunk_count": chunk_count,
        }

    @router.get("/v1/mail-todo/knowledge/documents")
    async def knowledge_documents(request: Request) -> dict[str, Any]:
        email_rag = runtime(request).email_rag
        documents: tuple[KnowledgeDocument, ...] = (
            email_rag.knowledge_documents if email_rag is not None else ()
        )
        items = [
            {
                "document_id": doc.document_id,
                "title": doc.title,
                "section_count": len(doc.chunks),
                "source_url": doc.source_url,
            }
            for doc in documents
        ]
        return {"documents": items}

    @router.post("/v1/mail-todo/knowledge/chat")
    async def knowledge_chat(body: KnowledgeChatRequest, request: Request) -> dict[str, Any]:
        email_rag = runtime(request).email_rag
        memory: SemanticMemoryPort = (
            email_rag.semantic_memory if email_rag is not None else NullSemanticMemory()
        )
        retrieval_request = SemanticRetrievalRequest(
            run_id="knowledge-adhoc",
            user_id="demo-gui",
            query=body.query,
            knowledge_gaps=(),
            filters=RetrievalFilters(document_status=("ready",)),
            limits=RetrievalLimits(top_k=body.top_k, min_score=-1.0, timeout_ms=8_000),
        )
        response = await memory.retrieve(retrieval_request)
        return response.to_dict()

    @router.post("/api/v1/raw-documents/upload")
    async def upload_raw_document(
        request: Request,
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename required")

        safe_name = Path(file.filename).name
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")

        ext = Path(safe_name).suffix.lower().lstrip(".")
        if ext not in ("pdf", "docx", "doc"):
            raise HTTPException(
                status_code=400,
                detail=f"Định dạng không được hỗ trợ: .{ext}. Chỉ chấp nhận .pdf, .docx, .doc",
            )

        target_raw = RAW_DOCS_DIR / safe_name
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Tệp rỗng")

        target_raw.write_bytes(content)
        repo = _raw_document_repo(request)
        await repo.record_save(safe_name, status=2)
        logger.info("Uploaded raw document %s into data/raw/ (%d bytes)", safe_name, len(content))

        # Auto-extract markdown into data/extracted/
        has_extracted = False
        extracted_name: str | None = None
        if ext in ("docx", "doc"):
            try:
                from cowork_agent.integrations.knowledge_ingestion.docx_extractor import (
                    DocxExtractor,
                )

                extracted = DocxExtractor().extract(target_raw)
                stem = target_raw.stem.lower().replace("_", "-").replace(" ", "-")
                extracted_name = f"{stem}.md"
                target_extracted = EXTRACTED_DIR / extracted_name
                target_extracted.write_text(extracted.markdown, encoding="utf-8")
                has_extracted = True
                logger.info("Extracted markdown for %s -> %s", safe_name, extracted_name)
            except Exception as extract_err:
                logger.warning("Could not extract markdown for %s: %s", safe_name, extract_err)
        elif ext == "pdf":
            try:
                from cowork_agent.integrations.knowledge_ingestion.pdf_inspector import (
                    PdfInspector,
                )

                inspection = PdfInspector().inspect(target_raw)
                pdf_text = "\n\n".join(inspection.native_markdown_by_page.values()).strip()
                if pdf_text:
                    stem = target_raw.stem.lower().replace("_", "-").replace(" ", "-")
                    extracted_name = f"{stem}.md"
                    target_extracted = EXTRACTED_DIR / extracted_name
                    target_extracted.write_text(pdf_text, encoding="utf-8")
                    has_extracted = True
                    logger.info("Extracted PDF text for %s -> %s", safe_name, extracted_name)
            except Exception as extract_err:
                logger.warning("Could not extract PDF text for %s: %s", safe_name, extract_err)

        return {
            "status": "uploaded",
            "filename": safe_name,
            "size": len(content),
            "file_type": ext,
            "has_extracted_md": has_extracted,
            "extracted_md_name": extracted_name,
        }

    @router.get("/api/v1/raw-documents")
    async def list_raw_documents() -> list[dict[str, Any]]:
        if not RAW_DOCS_DIR.exists():
            return []

        manifest = _load_raw_manifest()
        documents: list[dict[str, Any]] = []
        for item in sorted(
            RAW_DOCS_DIR.iterdir(),
            key=lambda p: p.stat().st_mtime if p.is_file() else 0,
            reverse=True,
        ):
            if not item.is_file() or item.name.startswith("."):
                continue
            try:
                stat = item.stat()
                extracted_md = _resolve_extracted_doc(item.name, manifest)
                documents.append(
                    {
                        "filename": item.name,
                        "file_type": item.suffix.lower().lstrip("."),
                        "size": stat.st_size,
                        "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                        "has_extracted_md": extracted_md is not None,
                        "extracted_md_name": extracted_md,
                    }
                )
            except Exception as exc:
                logger.warning("Failed to inspect raw document %s: %s", item.name, exc)
        return documents

    @router.get("/api/v1/raw-documents/{filename}")
    async def get_raw_document(filename: str) -> FileResponse:
        safe_name, target_path = _resolve_raw_document(filename)

        ext = target_path.suffix.lower()
        media_types = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc": "application/msword",
            ".txt": "text/plain",
            ".md": "text/markdown",
        }
        media_type = media_types.get(ext, "application/octet-stream")
        return FileResponse(
            path=target_path,
            media_type=media_type,
            filename=safe_name,
            content_disposition_type="inline",
        )

    @router.get("/api/v1/raw-documents/{filename}/extracted")
    async def get_raw_document_extracted_text(filename: str) -> dict[str, Any]:
        safe_name, _ = _resolve_raw_document(filename)

        extracted_md = _resolve_extracted_doc(safe_name, _load_raw_manifest())
        if not extracted_md:
            raise HTTPException(
                status_code=404, detail="Extracted markdown not found for this document"
            )

        content = (EXTRACTED_DIR / extracted_md).read_text(encoding="utf-8", errors="replace")
        return {
            "filename": safe_name,
            "extracted_md_name": extracted_md,
            "content": content,
        }

    @router.put("/api/v1/raw-documents/{filename}")
    async def put_raw_document(filename: str, request: Request) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")

        target_raw = RAW_DOCS_DIR / safe_name
        if not target_raw.is_file():
            raise HTTPException(status_code=404, detail="Raw document not found")

        content = await request.body()
        if not content:
            raise HTTPException(status_code=400, detail="Empty document payload")

        target_raw.write_bytes(content)
        repo = _raw_document_repo(request)
        await repo.record_save(safe_name, status=2)
        logger.info("Saved raw document %s directly (%d bytes)", safe_name, len(content))

        # Re-extract markdown if it was a docx file
        ext = target_raw.suffix.lower().lstrip(".")
        if ext in ("docx", "doc"):
            try:
                from cowork_agent.integrations.knowledge_ingestion.docx_extractor import (
                    DocxExtractor,
                )

                extracted = DocxExtractor().extract(target_raw)
                extracted_md_name = _resolve_extracted_doc(safe_name, _load_raw_manifest())
                if extracted_md_name:
                    target_extracted = EXTRACTED_DIR / extracted_md_name
                    target_extracted.write_text(extracted.markdown, encoding="utf-8")
                    logger.info("Updated extracted markdown for %s", safe_name)
            except Exception as extract_err:
                logger.warning("Could not re-extract markdown for %s: %s", safe_name, extract_err)

        return {"status": "saved", "filename": safe_name, "size": len(content)}

    @router.delete("/api/v1/raw-documents/{filename}")
    async def delete_raw_document(filename: str, request: Request) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid filename")

        target_raw = RAW_DOCS_DIR / safe_name
        if not target_raw.is_file():
            raise HTTPException(status_code=404, detail="Raw document not found")

        try:
            target_raw.unlink(missing_ok=True)
            logger.info("Deleted raw document %s", safe_name)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}") from exc

        repo = _raw_document_repo(request)
        try:
            await repo.delete(safe_name)
        except Exception as repo_err:
            logger.warning("Could not delete metadata for %s: %s", safe_name, repo_err)

        manifest = _load_raw_manifest()
        deleted_extracted: list[str] = []
        for target_extracted in _find_associated_extracted_docs(safe_name, manifest):
            try:
                target_extracted.unlink(missing_ok=True)
                deleted_extracted.append(target_extracted.name)
                logger.info(
                    "Deleted extracted markdown %s for %s", target_extracted.name, safe_name
                )
            except Exception as exc:
                logger.warning(
                    "Could not delete extracted markdown %s: %s", target_extracted.name, exc
                )

        return {"status": "deleted", "filename": safe_name, "deleted_extracted": deleted_extracted}

    return router


__all__ = ["KnowledgeChatRequest", "create_knowledge_router"]
