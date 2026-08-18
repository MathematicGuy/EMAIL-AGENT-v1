"""Worker startup configuration regression tests."""

from pathlib import Path

import pytest

from cowork_agent.orchestration import worker


def test_worker_loads_dotenv_before_requiring_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://user:pass@db.example/postgres\n")
    started = False

    async def fake_run_worker() -> None:
        nonlocal started
        started = True

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_MODE", raising=False)
    monkeypatch.setattr(worker, "run_worker", fake_run_worker)

    worker.main()

    assert started


def test_worker_selects_sqlite_document_runner_without_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("POSTGRES_MODE=off\nUSER_DOCUMENTS_ENABLED=false\n")
    started = False

    async def fake_run_sqlite_worker() -> None:
        nonlocal started
        started = True

    async def unexpected_postgres_worker() -> None:
        raise AssertionError("Postgres worker must not start in SQLite mode")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_MODE", raising=False)
    monkeypatch.setenv("LOG_FILE", "")
    monkeypatch.setattr(worker, "run_sqlite_worker", fake_run_sqlite_worker)
    monkeypatch.setattr(worker, "run_worker", unexpected_postgres_worker)

    worker.main()

    assert started
