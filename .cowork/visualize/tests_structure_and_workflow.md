# Test Suite Structure & Execution Workflow

## Testing Architecture & Directory Mapping Diagram

```mermaid
graph TD
    subgraph Suite ["Tests Directory (tests/)"]
        Unit["tests/unit/\n(Pure Python Unit Tests with Fake Adapters)"]
        Integ["tests/integration/\n(Database & API Route Integration Tests)"]
        Compat["tests/compatibility/\n(Schema & API Contract Compatibility Tests)"]
        Fixtures["tests/fixtures/\n(Reusable Mock Data & Email Envelopes)"]
    end

    subgraph Mirror ["Source Code Mirroring (src/cowork_agent/)"]
        Unit --> U1["tests/unit/domain/ ↔ src/cowork_agent/domain/"]
        Unit --> U2["tests/unit/features/ ↔ src/cowork_agent/features/"]
        Unit --> U3["tests/unit/integrations/ ↔ src/cowork_agent/integrations/"]
        Unit --> U4["tests/unit/persistence/ ↔ src/cowork_agent/persistence/"]
        Unit --> U5["tests/unit/orchestration/ ↔ src/cowork_agent/orchestration/"]
        
        Integ --> I1["tests/integration/api/ (FastAPI Endpoints)"]
        Integ --> I2["tests/integration/persistence/ (SQLite Repositories)"]
    end

    subgraph Commands ["Execution Commands"]
        C1["python -m pytest tests/unit/features -q\n(Fast targeted feature run)"]
        C2["python -m pytest -q\n(Full suite run)"]
        C3["python -m mypy src && python -m ruff check .\n(Quality Gates)"]
    end
```

## Key Test Workflow & Folder Concepts

1. **Mirroring Structure**: The unit test directory (`tests/unit/`) mirrors `src/cowork_agent/` 1-to-1, making it effortless to locate the test file for any target module.
2. **Verification Rule (from AGENTS.md)**:
   - Run small targeted pytest scope during active edits (e.g. `python -m pytest tests/unit/features -q`).
   - Run `ruff check` and `mypy` whenever `src/` changes.
   - Run full test suite (`python -m pytest -q`) before committing or when changing shared contracts (ports/schemas/migrations).
