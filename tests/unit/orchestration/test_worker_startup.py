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
    monkeypatch.setattr(worker, "run_worker", fake_run_worker)

    worker.main()

    assert started
