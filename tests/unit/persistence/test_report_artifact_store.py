"""Filesystem and in-memory report stores.

The filename rule itself is owned by ``unit/domain/test_report_artifacts.py``;
what is asserted here is that the store stays inside the folder it was given and
that both implementations answer the same interface.
"""

import asyncio
from pathlib import Path

import pytest

from cowork_agent.domain.report_artifacts import ReportArtifact, ReportFilename
from cowork_agent.persistence.report_artifacts import (
    FileSystemReportArtifactStore,
    InMemoryReportArtifactStore,
)


def _artifact(name: str, content: str = "# Bao cao\n\nNoi dung") -> ReportArtifact:
    return ReportArtifact(filename=ReportFilename.parse(name), content=content)


def test_save_creates_the_folder_and_round_trips(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "reports"
        store = FileSystemReportArtifactStore(root)

        stored = await store.save(_artifact("bao-cao.md", "noi dung"))

        assert stored.filename.value == "bao-cao.md"
        assert stored.content == "noi dung"
        assert (root / "bao-cao.md").read_text(encoding="utf-8") == "noi dung"

        read_back = await store.read(ReportFilename.parse("bao-cao.md"))
        assert read_back is not None
        assert read_back.content == "noi dung"

    asyncio.run(scenario())


def test_save_writes_nothing_outside_the_root(tmp_path: Path) -> None:
    """The store's own containment check, independent of the filename rule."""

    async def scenario() -> None:
        root = tmp_path / "reports"
        outside = tmp_path / "outside.md"
        store = FileSystemReportArtifactStore(root)

        await store.save(_artifact("bao-cao.md"))

        assert not outside.exists()
        assert [item.name for item in root.iterdir()] == ["bao-cao.md"]

    asyncio.run(scenario())


def test_listing_is_newest_first_and_skips_dotfiles_and_directories(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "reports"
        root.mkdir()
        (root / ".hidden.md").write_text("hidden", encoding="utf-8")
        (root / "nested").mkdir()
        store = FileSystemReportArtifactStore(root)

        await store.save(_artifact("older.md", "older"))
        # ``os.utime`` rather than a sleep: ordering is the subject, not timing.
        import os

        os.utime(root / "older.md", (1_700_000_000, 1_700_000_000))
        await store.save(_artifact("newer.md", "newer"))
        os.utime(root / "newer.md", (1_800_000_000, 1_800_000_000))

        names = [report.filename.value for report in await store.list_reports()]
        assert names == ["newer.md", "older.md"]

    asyncio.run(scenario())


def test_read_and_delete_report_absence_rather_than_raising(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = FileSystemReportArtifactStore(tmp_path / "reports")

        assert await store.read(ReportFilename.parse("missing.md")) is None
        assert await store.delete(ReportFilename.parse("missing.md")) is False

        await store.save(_artifact("present.md"))
        assert await store.delete(ReportFilename.parse("present.md")) is True
        assert await store.read(ReportFilename.parse("present.md")) is None

    asyncio.run(scenario())


def test_path_for_only_answers_for_a_file_that_exists(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "reports"
        store = FileSystemReportArtifactStore(root)
        name = ReportFilename.parse("bao-cao.md")

        assert store.path_for(name) is None
        await store.save(_artifact("bao-cao.md"))
        assert store.path_for(name) == root / "bao-cao.md"

    asyncio.run(scenario())


def test_listing_survives_a_file_it_cannot_name(tmp_path: Path) -> None:
    """A stray file must not take the whole artifacts view down."""

    async def scenario() -> None:
        root = tmp_path / "reports"
        root.mkdir()
        (root / ("x" * 200 + ".md")).write_text("stray", encoding="utf-8")
        store = FileSystemReportArtifactStore(root)

        await store.save(_artifact("bao-cao.md"))

        names = [report.filename.value for report in await store.list_reports()]
        assert names == ["bao-cao.md"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "store_factory",
    [
        lambda tmp: FileSystemReportArtifactStore(tmp / "reports"),
        lambda tmp: InMemoryReportArtifactStore(),
    ],
    ids=["filesystem", "in-memory"],
)
def test_both_stores_answer_the_same_interface(store_factory, tmp_path: Path) -> None:
    async def scenario() -> None:
        store = store_factory(tmp_path)
        name = ReportFilename.parse("bao-cao.md")

        stored = await store.save(_artifact("bao-cao.md", "noi dung"))
        assert stored.filename == name
        assert stored.size > 0

        assert [item.filename for item in await store.list_reports()] == [name]
        assert (await store.read(name)) is not None
        assert await store.delete(name) is True
        assert await store.list_reports() == ()

    asyncio.run(scenario())


def test_in_memory_store_orders_newest_first_without_a_clock() -> None:
    async def scenario() -> None:
        store = InMemoryReportArtifactStore()
        await store.save(_artifact("first.md"))
        await store.save(_artifact("second.md"))

        names = [report.filename.value for report in await store.list_reports()]
        assert names == ["second.md", "first.md"]

    asyncio.run(scenario())
