"""Startup loop selection regression tests."""

from pathlib import Path
from typing import Any

import pytest

from cowork_agent import app


def test_main_loads_dotenv_before_selecting_windows_postgres_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Select the Selector loop when DATABASE_URL is supplied only by .env."""
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://user:pass@db.example/postgres\n"
    )
    captured: dict[str, Any] = {}

    def captured_run(*args: Any, **kwargs: Any) -> None:
        del args
        captured.update(kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.uvicorn, "run", captured_run)

    app.main()

    assert captured["loop"] == "asyncio:SelectorEventLoop"
