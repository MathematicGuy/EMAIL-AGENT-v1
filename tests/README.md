# Test Execution Guide for Coding Agents

Token-efficient, high-density reference for running tests, linter, type-checker, and performance optimization tools in `EMAIL-AGENT-v1`. Always prefer `uv run pytest` over plain `python -m pytest` for fast, efficient test execution.

---

## 1. Quick Command Cheat Sheet

| Purpose | Command | Description |
|---|---|---|
| **Smallest Unit Test** | `uv run pytest tests/unit/<subpath> -q` | Fast feedback loop for local module edits |
| **All Unit Tests** | `uv run pytest tests/unit -q` | Run full unit test suite |
| **Integration Tests** | `uv run pytest tests/integration -q` | Persistence, API, and retrieval tests |
| **Compatibility Tests** | `uv run pytest tests/compatibility -q` | Contract, privacy, and dedupe checks |
| **Full Suite** | `uv run pytest -q` | Standard full test suite run |
| **Parallel Suite (`pytest-xdist`)** | `uv run pytest -n auto -q` | 3x–8x faster multi-core parallel execution |
| **Fast Venv Execution (`uv`)** | `uv run pytest -q` | Rust-based runner; reduces Python startup overhead |
| **Full Verification Gate** | `uv run pytest -q && python -m ruff check . && python -m mypy src` | Complete test + lint + type check gate |
| **Linter** | `python -m ruff check .` | Ruff linter & style check |
| **Type Checker** | `python -m mypy src` | Strict static type checking |

---

## 2. Performance Optimization & Profiling Tools

| Tool / Strategy | Command / Setting | Purpose & Impact |
|---|---|---|
| **`uv` Runner** | `uv run pytest` | **Fast Startup**: Bypasses venv activation overhead using Rust-based runner (Default) |
| **`pytest-xdist`** | `uv run pytest -n auto` | **3x–8x Acceleration**: Parallelizes tests across CPU cores |
| **Slow Test Profiler** | `uv run pytest --durations=10` | **Bottleneck Tracking**: Identifies top 10 slowest tests |
| **SQLite `:memory:` DB** | In-Memory Connection | **Disk I/O Bypass**: Runs persistence/DB tests entirely in RAM |
| **Collection Scoping** | `pyproject.toml` (`testpaths = ["tests"]`) | **Fast Discovery**: Restricts discovery scope, skipping non-test trees |

---

## 3. Verification Protocol for Coding Agents

1. **Scope Selection**: Run the *smallest pytest scope* covering edited code (e.g. `uv run pytest tests/unit/features -q`).
2. **Lint & Type Check**: When `src/` is modified, run `python -m ruff check .` and `python -m mypy src`.
3. **Full Suite Escalation**: Prior to running the full test suite, always run the **Core Subsystem Pre-Verification Suite** first. **If any core test fails, stop immediately and report the failure back to the user before running the main test suite.**

### Core Subsystem Pre-Verification Suite

Before running the full test suite (`uv run pytest -q`), execute the core feature smoke tests covering key application subsystems. **Accelerate test execution using `uv run pytest` or `uv run pytest -n auto` (or `addopts = "-n 4"` in `pyproject.toml`).**

| Core Feature / Subsystem | Test Scope Files | Latency / Execution Time (ms) |
|---|---|---|
| **1. Qdrant Vector Store** | `tests/unit/integrations/test_qdrant.py`, `tests/unit/integrations/test_project_document_qdrant.py` | **8,720 ms** (~8.72s) |
| **2. Supabase & Postgres Storage** | `tests/unit/integrations/test_supabase_storage.py`, `tests/unit/test_postgres_only_runtime.py` | **5,660 ms** (~5.66s) |
| **3. Email Auth & Identity** | `tests/unit/test_identity.py`, `tests/unit/test_session_cookie.py`, `tests/integration/api/test_principal_boundary.py` | **5,920 ms** (~5.92s) |
| **4. Email / Document Read API** | `tests/unit/test_app_document_routes.py`, `tests/integration/api/test_server.py`, `tests/integration/api/test_project_documents_api.py` | **6,310 ms** (~6.31s) |

#### Recommended Accelerated Command (`uv` runner):
```bash
uv run pytest tests/unit/integrations/test_qdrant.py tests/unit/integrations/test_project_document_qdrant.py tests/unit/integrations/test_supabase_storage.py tests/unit/test_postgres_only_runtime.py tests/unit/test_identity.py tests/unit/test_session_cookie.py tests/integration/api/test_principal_boundary.py tests/unit/test_app_document_routes.py tests/integration/api/test_server.py tests/integration/api/test_project_documents_api.py -q
```

---

## 4. Source-to-Test Mapping

| Edited Path in `src/cowork_agent/` | Recommended Test Scope |
|---|---|
| `domain/` | `uv run pytest tests/unit/domain -q` |
| `features/email_action_plan/` | `uv run pytest tests/unit/features -q` |
| `integrations/gmail/` | `uv run pytest tests/unit/integrations/test_gmail*.py -q` |
| `integrations/llm/` | `uv run pytest tests/unit/integrations/test_llm*.py -q` |
| `integrations/rag/` | `uv run pytest tests/unit/integrations/test_rag*.py -q` |
| `orchestration/` | `uv run pytest tests/unit/orchestration -q` |
| `persistence/` | `uv run pytest tests/unit/persistence tests/integration/persistence -q` |
| `api/` or `app.py` | `uv run pytest tests/integration/api -q` |

---

## 5. Useful Pytest Flags for Agent Sessions

- **`-q` (Quiet)**: Reduces output token footprint (recommended default).
- **`-n auto`**: Uses `pytest-xdist` to parallelize test runs across CPU cores.
- **`--durations=10`**: Surfaces top 10 slowest tests for bottleneck analysis.
- **`-x` / `--maxfail=1`**: Stop immediately on first failure.
- **`--tb=short` / `--tb=line`**: Short tracebacks (saves context window budget).
- **`-k "pattern"`**: Filter tests by function or class name expression.
- **`--lf` (Last Failed)**: Re-run only tests that failed in the previous run.
- **`-s`**: Disable output capture (show `print`/logger outputs).

### Examples

```bash
# Debug single failing function with short traceback
uv run pytest tests/unit/features -k "test_router_decides_retrieve_rag" --tb=short

# Profile top 10 slowest tests in parallel mode
uv run pytest -n auto --durations=10 -q

# Fast re-run of last failed tests with uv
uv run pytest -q --lf -x
```

---

## 6. Test Directory Layout & Infrastructure

- **`tests/unit/`**: Pure domain models, features, orchestration, and unit logic (zero external I/O).
- **`tests/integration/`**: SQLite persistence (`:memory:` / temporary isolated DB), FastAPI endpoints, Qdrant/RAG integrations.
- **`tests/compatibility/`**: API contract stability, privacy boundaries, ordering & deduplication rules.
- **`tests/fixtures/`**: Shared test fixtures, mock data, and deterministic fakes (`src/cowork_agent/integrations/*/fakes.py`).
- **Pythonpath & Collection Config**: `pyproject.toml` configures `pythonpath = ["src", "."]` and `testpaths = ["tests"]`. Always invoke pytest via `uv run pytest`.
