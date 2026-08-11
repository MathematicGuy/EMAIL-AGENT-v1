# Test Execution Guide for Coding Agents

Token-efficient, high-density reference for running tests, linter, and type-checker in `EMAIL-AGENT-v1`.

---

## 1. Quick Command Cheat Sheet

| Purpose | Command | Description |
|---|---|---|
| **Smallest Unit Test** | `python -m pytest tests/unit/<subpath> -q` | Fast feedback loop for local module edits |
| **All Unit Tests** | `python -m pytest tests/unit -q` | Run full unit test suite |
| **Integration Tests** | `python -m pytest tests/integration -q` | Persistence, API, and retrieval tests |
| **Compatibility Tests** | `python -m pytest tests/compatibility -q` | Contract, privacy, and dedupe checks |
| **Full Suite** | `python -m pytest -q` | Standard full test suite run |
| **Parallel Suite** | `python -m pytest -n auto -q` | Multi-core parallel execution (via `pytest-xdist`) |
| **Full Verification Gate** | `python -m pytest -q && python -m ruff check . && python -m mypy src` | Complete test + lint + type check gate |
| **Linter** | `python -m ruff check .` | Ruff linter & style check |
| **Type Checker** | `python -m mypy src` | Strict static type checking |

---

## 2. Verification Protocol for Coding Agents

1. **Scope Selection**: Run the *smallest pytest scope* covering edited code (e.g. `python -m pytest tests/unit/features -q`).
2. **Lint & Type Check**: When `src/` is modified, run `python -m ruff check .` and `python -m mypy src`.
3. **Full Suite Escalation**: Expand to `python -m pytest -q` only if:
   - Shared contracts change (`domain/`, `ports.py`, `schemas.py`, DB migrations).
   - Targeted unit tests fail and regression risk requires full verification.

---

## 3. Source-to-Test Mapping

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
| `gui/` | `python -m pytest tests/unit/gui -q` |

---

## 4. Useful Pytest Flags for Agent Sessions

- **`-q` (Quiet)**: Reduces output token footprint (recommended default).
- **`-x` / `--maxfail=1`**: Stop immediately on first failure.
- **`--tb=short` / `--tb=line`**: Short tracebacks (saves context window budget).
- **`-k "pattern"`**: Filter tests by function or class name expression.
- **`--lf` (Last Failed)**: Re-run only tests that failed in the previous run.
- **`-s`**: Disable output capture (show `print`/logger outputs).

### Examples

```bash
# Debug single failing function with short traceback
python -m pytest tests/unit/features -k "test_router_decides_retrieve_rag" --tb=short

# Fast re-run of last failed tests
python -m pytest -q --lf -x
```

---

## 5. Test Directory Layout & Infrastructure

- **`tests/unit/`**: Pure domain models, features, orchestration, and unit logic (zero external I/O).
- **`tests/integration/`**: SQLite persistence, FastAPI endpoints, Qdrant/RAG integrations.
- **`tests/compatibility/`**: API contract stability, privacy boundaries, ordering & deduplication rules.
- **`tests/fixtures/`**: Shared test fixtures, mock data, and deterministic fakes (`src/cowork_agent/integrations/*/fakes.py`).
- **Pythonpath Config**: `pyproject.toml` configures `pythonpath = ["src", "."]`. Always invoke pytest via `python -m pytest`.
