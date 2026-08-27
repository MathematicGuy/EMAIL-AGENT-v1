"""Startup loop selection regression tests."""

from pathlib import Path
from typing import Any

import pytest

from cowork_agent import app


def test_main_loads_dotenv_before_selecting_windows_postgres_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Select the Selector loop when DATABASE_URL is supplied only by .env."""
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://user:pass@db.example/postgres\n")
    captured: dict[str, Any] = {}

    def captured_run(*args: Any, **kwargs: Any) -> None:
        del args
        captured.update(kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_MODE", raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.uvicorn, "run", captured_run)

    app.main()

    assert captured["loop"] == "asyncio:SelectorEventLoop"
    assert captured["workers"] == 1


def test_main_passes_reload_and_src_reload_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def captured_run(*args: Any, **kwargs: Any) -> None:
        del args
        captured.update(kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(app.sys, "argv", ["mail-todo-api", "--reload"])
    monkeypatch.setattr(app.uvicorn, "run", captured_run)

    app.main()

    assert captured["reload"] is True
    assert captured["reload_dirs"] is not None
    assert str(Path(app.__file__).resolve().parent.parent) in captured["reload_dirs"][0]
