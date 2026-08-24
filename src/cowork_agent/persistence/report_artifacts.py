"""Report artifact stores: one filesystem adapter, one in-memory test double.

The folder is supplied at construction. Nothing here walks ``__file__`` to find
it, which is what let three call sites disagree about where reports live.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.domain.report_artifacts import (
    ReportArtifact,
    ReportFilename,
    StoredReport,
)

logger = logging.getLogger(__name__)


class FileSystemReportArtifactStore:
    """Reports as files in one directory, one level deep."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def location(self) -> Path:
        return self._root

    def _target(self, filename: ReportFilename) -> Path:
        """Resolve to a direct child of the root, or refuse.

        ``ReportFilename`` has already ruled out a directory part, so the only
        thing left for this check to catch is a symlink inside the folder that
        points somewhere else — the same hazard ``_resolve_raw_document`` guards
        for the raw corpus.
        """
        target = self._root / filename.value
        if target.resolve().parent != self._root.resolve():
            raise ValueError(f"Report {filename.value!r} escapes {self._root}")
        return target

    def _stored(self, target: Path, content: str) -> StoredReport:
        stat = target.stat()
        return StoredReport(
            filename=ReportFilename(target.name),
            content=content,
            size=stat.st_size,
            updated_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        )

    def _save(self, artifact: ReportArtifact) -> StoredReport:
        target = self._target(artifact.filename)
        self._root.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.content, encoding="utf-8")
        return self._stored(target, artifact.content)

    async def save(self, artifact: ReportArtifact) -> StoredReport:
        return await asyncio.to_thread(self._save, artifact)

    def _list_reports(self) -> tuple[StoredReport, ...]:
        if not self._root.is_dir():
            return ()
        reports: list[StoredReport] = []
        for item in sorted(
            self._root.iterdir(),
            key=lambda path: path.stat().st_mtime if path.is_file() else 0,
            reverse=True,
        ):
            if not item.is_file() or item.name.startswith("."):
                continue
            try:
                content = item.read_text(encoding="utf-8", errors="replace")
                reports.append(self._stored(item, content))
            except (OSError, ValueError) as exc:
                # A file the folder picked up from elsewhere — an unreadable
                # name, a vanished file — must not take the whole listing down.
                logger.warning("Skipping unreadable report %s: %s", item.name, exc)
        return tuple(reports)

    async def list_reports(self) -> tuple[StoredReport, ...]:
        return await asyncio.to_thread(self._list_reports)

    def _read(self, filename: ReportFilename) -> StoredReport | None:
        target = self._target(filename)
        if not target.is_file():
            return None
        return self._stored(target, target.read_text(encoding="utf-8", errors="replace"))

    async def read(self, filename: ReportFilename) -> StoredReport | None:
        return await asyncio.to_thread(self._read, filename)

    def _delete(self, filename: ReportFilename) -> bool:
        target = self._target(filename)
        if not target.is_file():
            return False
        target.unlink()
        return True

    async def delete(self, filename: ReportFilename) -> bool:
        return await asyncio.to_thread(self._delete, filename)

    def path_for(self, filename: ReportFilename) -> Path | None:
        target = self._target(filename)
        return target if target.is_file() else None


class InMemoryReportArtifactStore:
    """Same interface, no disk. The seam tests substitute across."""

    def __init__(self, *, location: Path | None = None) -> None:
        self._location = location or Path("memory://reports")
        self._reports: dict[str, StoredReport] = {}
        self._sequence = 0

    @property
    def location(self) -> Path:
        return self._location

    def _next_timestamp(self) -> datetime:
        # Monotonic without a clock so ``list_reports`` ordering is assertable.
        self._sequence += 1
        return datetime.fromtimestamp(self._sequence, UTC)

    async def save(self, artifact: ReportArtifact) -> StoredReport:
        stored = StoredReport(
            filename=artifact.filename,
            content=artifact.content,
            size=len(artifact.content.encode("utf-8")),
            updated_at=self._next_timestamp(),
        )
        self._reports[artifact.filename.value] = stored
        return stored

    async def list_reports(self) -> tuple[StoredReport, ...]:
        return tuple(
            sorted(self._reports.values(), key=lambda item: item.updated_at, reverse=True)
        )

    async def read(self, filename: ReportFilename) -> StoredReport | None:
        return self._reports.get(filename.value)

    async def delete(self, filename: ReportFilename) -> bool:
        return self._reports.pop(filename.value, None) is not None

    def path_for(self, filename: ReportFilename) -> Path | None:
        return None


def reveal_directory(path: Path) -> None:
    """Open ``path`` in the host file manager.

    Separate from the store: revealing a folder is an operator convenience on
    the machine serving the API, not something a report store does.
    """
    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    # ``getattr`` rather than ``os.startfile``: the attribute is Windows-only, so
    # naming it directly fails type checking on a Linux CI runner.
    start_file: Callable[[str], object] | None = getattr(os, "startfile", None)
    if start_file is not None:
        start_file(str(resolved))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(resolved)])  # noqa: S603, S607


__all__ = [
    "FileSystemReportArtifactStore",
    "InMemoryReportArtifactStore",
    "reveal_directory",
]
