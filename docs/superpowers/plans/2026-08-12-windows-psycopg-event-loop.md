# Windows Psycopg Event Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure `mail-todo-api` selects a psycopg-compatible event loop on Windows when `DATABASE_URL` is provided by `.env`.

**Architecture:** The executable loads `.env` before the existing platform and database conditional. Uvicorn continues to receive its custom selector factory only for Windows PostgreSQL mode and receives `auto` in all other modes.

**Tech Stack:** Python 3.12, python-dotenv, Uvicorn, pytest.

## Global Constraints

- Do not expose database credentials in code, test output, or documentation.
- Preserve Uvicorn's `auto` loop for non-Windows and database-free local mode.
- Use a temporary `.env` and a boundary stub for `uvicorn.run`; do not start a server in the unit test.

---

### Task 1: Load `.env` before selecting the Uvicorn loop

**Files:**
- Modify: `src/cowork_agent/app.py: imports and main()`
- Create: `tests/unit/test_windows_psycopg_startup.py`

**Interfaces:**
- Consumes: `dotenv.load_dotenv(override=False)` and `database_url()`.
- Produces: `main()` calls `uvicorn.run(..., loop="asyncio:SelectorEventLoop")` for a Windows process whose temporary `.env` contains `DATABASE_URL`.

- [ ] **Step 1: Write the failing test**

```python
def test_main_loads_dotenv_before_selecting_windows_postgres_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://user:pass@db.example/postgres\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.uvicorn, "run", captured_run)

    app.main()

    assert captured["loop"] == "asyncio:SelectorEventLoop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_windows_psycopg_startup.py -q`

Expected: FAIL because `main()` reads `DATABASE_URL` before loading `.env` and passes `loop="auto"`.

- [ ] **Step 3: Write minimal implementation**

```python
from dotenv import load_dotenv

def main() -> None:
    load_dotenv(override=False)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    loop = "auto"
```

- [ ] **Step 4: Run focused verification**

Run: `python -m pytest tests/unit/test_windows_psycopg_startup.py -q`

Expected: PASS.

- [ ] **Step 5: Run static checks and commit**

Run: `python -m ruff check src/cowork_agent/app.py tests/unit/test_windows_psycopg_startup.py && python -m mypy src/cowork_agent/app.py`

Commit:

```text
fix(app): load environment before selecting Windows loop
```
