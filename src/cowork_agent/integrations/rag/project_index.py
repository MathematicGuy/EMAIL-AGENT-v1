"""Per-project Turbovec vector index, one ``.tvim`` snapshot per project.

Physical partitioning is the point: a project can only ever be searched
against its own file, so cross-tenant leakage has a structural backstop rather
than resting on a code invariant alone (ADR-008 decision 4). The remaining five
ADR-007 conditions arrive as a ``uint64`` allowlist computed in SQL.

Two processes share these files. ``mail-todo-worker`` writes them and pushes a
snapshot to private object storage; ``mail-todo-api`` pulls and reads them.
Every local write goes to a temporary file and is then atomically renamed, so a
reader never observes a torn index.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Protocol

import numpy as np

try:
    from turbovec import IdMapIndex  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only where turbovec is absent
    IdMapIndex = None

from cowork_agent.observability import (
    ProjectDocumentTimingOutcome,
    log_project_document_timing,
    safe_provider_label,
)

from .turbovec_memory import TURBOVEC_AVAILABLE, _pad_vector_dim

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "project-indexes"


class ProjectIndexUnavailable(RuntimeError):
    """The project's index could not be opened, so retrieval must degrade.

    Never recovered by rebuilding in the request path: reconstructing a
    ``.tvim`` means re-embedding every chunk, which would put unbounded
    embedding I/O behind a user's query.
    """


class SnapshotStorage(Protocol):
    """The subset of private object storage a snapshot needs."""

    async def download_to(self, object_key: str, target: Path) -> None: ...

    async def upload_file(self, object_key: str, source: Path) -> None: ...

    async def object_exists(self, object_key: str) -> bool: ...


class TurbovecProjectIndexStore:
    """Owns the ``.tvim`` lifecycle for every project on this host."""

    def __init__(
        self,
        root: Path | str,
        *,
        storage: SnapshotStorage | None = None,
        vector_size: int = 3072,
        bit_width: int = 4,
    ) -> None:
        if not TURBOVEC_AVAILABLE:
            raise RuntimeError("turbovec package is required for the project document plane")
        if vector_size < 1:
            raise ValueError("vector_size must be positive")
        self._root = Path(root)
        self._storage = storage
        self._vector_size = vector_size
        self._bit_width = bit_width
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def root(self) -> Path:
        """Directory every ``.tvim`` for this host lives in; probed by health."""
        return self._root

    def index_path(self, project_id: str) -> Path:
        _require_project(project_id)
        return self._root / f"{project_id}.tvim"

    def snapshot_key(self, project_id: str) -> str:
        _require_project(project_id)
        return f"{SNAPSHOT_PREFIX}/{project_id}.tvim"

    async def add(
        self,
        *,
        project_id: str,
        document_id: str | None = None,
        vector_ids: Sequence[int],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        """Add or replace vectors, then publish a fresh snapshot."""
        if len(vector_ids) != len(vectors) or not vector_ids:
            raise ValueError("project index writes require matching, non-empty vectors and ids")
        matrix = self._normalize(vectors)
        ids = np.asarray(vector_ids, dtype=np.uint64)
        async with self._lock(project_id):
            stage_started = perf_counter()
            stage_outcome: ProjectDocumentTimingOutcome = "error"
            snapshot_bytes: int | None = None
            try:
                index = await asyncio.to_thread(self._open_or_create, project_id)
                await asyncio.to_thread(self._add_replacing, index, matrix, ids)
                target = await self._write_local(project_id, index)
                snapshot_bytes = target.stat().st_size
                stage_outcome = "success"
            finally:
                log_project_document_timing(
                    logger,
                    stage="local_index_update",
                    started=stage_started,
                    outcome=stage_outcome,
                    document_id=document_id,
                    project_id=project_id,
                    snapshot_bytes=snapshot_bytes,
                    provider=safe_provider_label(self),
                )
            storage = self._storage
            if storage is not None:
                stage_started = perf_counter()
                stage_outcome = "error"
                try:
                    await storage.upload_file(self.snapshot_key(project_id), target)
                    stage_outcome = "success"
                finally:
                    log_project_document_timing(
                        logger,
                        stage="snapshot_upload",
                        started=stage_started,
                        outcome=stage_outcome,
                        document_id=document_id,
                        project_id=project_id,
                        snapshot_bytes=snapshot_bytes,
                        provider=safe_provider_label(storage),
                    )

    async def remove(self, *, project_id: str, vector_ids: Sequence[int]) -> None:
        """Drop vectors by external ID; missing IDs are not an error."""
        if not vector_ids:
            return
        async with self._lock(project_id):
            if not self.index_path(project_id).exists():
                return
            index = await asyncio.to_thread(self._load_local, project_id)
            await asyncio.to_thread(_remove_all, index, vector_ids)
            await self._publish(project_id, index)

    async def search(
        self,
        *,
        project_id: str,
        vector: Sequence[float],
        allowlist: Sequence[int],
        limit: int,
    ) -> tuple[tuple[int, float], ...]:
        """Return ``(vector_id, score)`` pairs restricted to ``allowlist``.

        The allowlist is intersected with the index first: Postgres may list a
        chunk whose vector has not reached this snapshot yet, and Turbovec
        raises ``KeyError`` for an unknown allowlist ID rather than skipping it.
        """
        if not allowlist or limit < 1:
            return ()
        index = await self._open_for_read(project_id)
        query = self._normalize((vector,))
        return await asyncio.to_thread(_search, index, query, allowlist, limit)

    async def drop(self, project_id: str) -> None:
        """Remove a project's index entirely; used when the project is deleted."""
        async with self._lock(project_id):
            self.index_path(project_id).unlink(missing_ok=True)

    def _lock(self, project_id: str) -> asyncio.Lock:
        _require_project(project_id)
        return self._locks.setdefault(project_id, asyncio.Lock())

    def _normalize(self, vectors: Sequence[Sequence[float]]) -> np.ndarray:
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != self._vector_size:
            raise ValueError("project vectors do not match the configured embedding dimension")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        padded, _ = _pad_vector_dim((matrix / norms).astype(np.float32))
        return padded

    def _padded_dim(self) -> int:
        remainder = self._vector_size % 8
        return self._vector_size if remainder == 0 else self._vector_size + (8 - remainder)

    def _open_or_create(self, project_id: str) -> IdMapIndex:
        path = self.index_path(project_id)
        if path.exists():
            return IdMapIndex.load(str(path))
        return IdMapIndex(dim=self._padded_dim(), bit_width=self._bit_width)

    def _load_local(self, project_id: str) -> IdMapIndex:
        return IdMapIndex.load(str(self.index_path(project_id)))

    @staticmethod
    def _add_replacing(index: IdMapIndex, matrix: np.ndarray, ids: np.ndarray) -> None:
        # add_with_ids rejects an id the index already holds, and a retried
        # ingestion deliberately re-uses the same vector_id values.
        for vector_id in ids.tolist():
            index.remove(int(vector_id))
        index.add_with_ids(matrix, ids)

    async def _open_for_read(self, project_id: str) -> IdMapIndex:
        path = self.index_path(project_id)
        if not path.exists():
            await self._pull_snapshot(project_id)
        if not path.exists():
            raise ProjectIndexUnavailable(f"no vector index for project {project_id}")
        try:
            return await asyncio.to_thread(self._load_local, project_id)
        except Exception as exc:
            raise ProjectIndexUnavailable(
                f"unreadable vector index for project {project_id}"
            ) from exc

    async def _pull_snapshot(self, project_id: str) -> None:
        if self._storage is None:
            return
        key = self.snapshot_key(project_id)
        target = self.index_path(project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(".tvim.download")
        try:
            if not await self._storage.object_exists(key):
                return
            await self._storage.download_to(key, staging)
            os.replace(staging, target)
        except Exception:
            staging.unlink(missing_ok=True)
            logger.warning("Project index snapshot download failed; project_id=%s", project_id)

    async def _publish(self, project_id: str, index: IdMapIndex) -> None:
        target = await self._write_local(project_id, index)
        if self._storage is not None:
            await self._storage.upload_file(self.snapshot_key(project_id), target)

    async def _write_local(self, project_id: str, index: IdMapIndex) -> Path:
        target = self.index_path(project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(".tvim.writing")
        await asyncio.to_thread(index.write, str(staging))
        os.replace(staging, target)
        return target


def _remove_all(index: IdMapIndex, vector_ids: Sequence[int]) -> None:
    for vector_id in vector_ids:
        index.remove(int(vector_id))


def _search(
    index: IdMapIndex,
    query: np.ndarray,
    allowlist: Sequence[int],
    limit: int,
) -> tuple[tuple[int, float], ...]:
    present = [int(vector_id) for vector_id in allowlist if index.contains(int(vector_id))]
    if not present:
        return ()
    scores, ids = index.search(query, k=limit, allowlist=np.asarray(present, dtype=np.uint64))
    return tuple(
        (int(vector_id), float(score)) for score, vector_id in zip(scores[0], ids[0], strict=True)
    )


def _require_project(project_id: str) -> None:
    if not project_id.strip() or "/" in project_id or "\\" in project_id or ".." in project_id:
        raise ValueError("project_id must be a bare identifier")
