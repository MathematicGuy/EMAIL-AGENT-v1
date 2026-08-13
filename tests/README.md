# Test Execution Guide for Coding Agents

Token-efficient, high-density reference for running tests, linter, type-checker, and performance optimization tools in `EMAIL-AGENT-v1`.

---

## 1. Quick Command Cheat Sheet

| Purpose | Command | Description |
|---|---|---|
| **Smallest Unit Test** | `python -m pytest tests/unit/<subpath> -q` | Fast feedback loop for local module edits |
| **All Unit Tests** | `python -m pytest tests/unit -q` | Run full unit test suite |
| **Integration Tests** | `python -m pytest tests/integration -q` | Persistence, API, and retrieval tests |
| **Compatibility Tests** | `python -m pytest tests/compatibility -q` | Contract, privacy, and dedupe checks |
| **Full Suite** | `python -m pytest -q` | Standard full test suite run |
| **Parallel Suite (`pytest-xdist`)** | `python -m pytest -n auto -q` | 3x–8x faster multi-core parallel execution |
| **Fast Venv Execution (`uv`)** | `uv run pytest -q` | Rust-based runner; reduces Python startup overhead |
| **Full Verification Gate** | `python -m pytest -q && python -m ruff check . && python -m mypy src` | Complete test + lint + type check gate |
| **Linter** | `python -m ruff check .` | Ruff linter & style check |
| **Type Checker** | `python -m mypy src` | Strict static type checking |

---

## 2. Performance Optimization & Profiling Tools

| Tool / Strategy | Command / Setting | Purpose & Impact |
|---|---|---|
| **`pytest-xdist`** | `pytest -n auto` | **3x–8x Acceleration**: Parallelizes tests across all CPU cores |
| **`uv` Runner** | `uv run pytest` | **Fast Startup**: Bypasses venv activation overhead using Rust-based runner |
| **Slow Test Profiler** | `pytest --durations=10` | **Bottleneck Tracking**: Identifies top 10 slowest tests |
| **SQLite `:memory:` DB** | In-Memory Connection | **Disk I/O Bypass**: Runs persistence/DB tests entirely in RAM |
| **Collection Scoping** | `pyproject.toml` (`testpaths = ["tests"]`) | **Fast Discovery**: Restricts discovery scope, skipping non-test trees |

---

## 3. Verification Protocol for Coding Agents

1. **Scope Selection**: Run the *smallest pytest scope* covering edited code (e.g. `python -m pytest tests/unit/features -q`).
2. **Lint & Type Check**: When `src/` is modified, run `python -m ruff check .` and `python -m mypy src`.
3. **Full Suite Escalation**: Expand to `python -m pytest -q` or `python -m pytest -n auto -q` only if:
   - Shared contracts change (`domain/`, `ports.py`, `schemas.py`, DB migrations).
   - Targeted unit tests fail and regression risk requires full verification.

---

## 4. Source-to-Test Mapping

| Edited Path in `src/cowork_agent/` | Recommended Test Scope |
|---|---|
| `domain/` | `python -m pytest tests/unit/domain -q` |
| `features/email_action_plan/` | `python -m pytest tests/unit/features -q` |
| `integrations/gmail/` | `python -m pytest tests/unit/integrations/test_gmail*.py -q` |
| `integrations/llm/` | `python -m pytest tests/unit/integrations/test_llm*.py -q` |
| `integrations/rag/` | `python -m pytest tests/unit/integrations/test_rag*.py -q` |
| `orchestration/` | `python -m pytest tests/unit/orchestration -q` |
| `persistence/` | `python -m pytest tests/unit/persistence tests/integration/persistence -q` |
| `api/` or `app.py` | `python -m pytest tests/integration/api -q` |

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
python -m pytest tests/unit/features -k "test_router_decides_retrieve_rag" --tb=short

# Profile top 10 slowest tests in parallel mode
python -m pytest -n auto --durations=10 -q

# Fast re-run of last failed tests with uv
uv run pytest -q --lf -x
```

---

## 6. Test Directory Layout & Infrastructure

- **`tests/unit/`**: Pure domain models, features, orchestration, and unit logic (zero external I/O).
- **`tests/integration/`**: SQLite persistence (`:memory:` / temporary isolated DB), FastAPI endpoints, Qdrant/RAG integrations.
- **`tests/compatibility/`**: API contract stability, privacy boundaries, ordering & deduplication rules.
- **`tests/fixtures/`**: Shared test fixtures, mock data, and deterministic fakes (`src/cowork_agent/integrations/*/fakes.py`).
- **Pythonpath & Collection Config**: `pyproject.toml` configures `pythonpath = ["src", "."]` and `testpaths = ["tests"]`. Always invoke pytest via `python -m pytest` or `uv run pytest`.
