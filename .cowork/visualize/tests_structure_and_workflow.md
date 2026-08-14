# Test Suite Structure & Execution Workflow

> **The authoritative route index is [`tests/README.md`](../../tests/README.md).**
> This page is the picture only. It deliberately carries no route timings, no
> invariant list, and no command defaults — those live in one place so they
> cannot drift apart.

## Testing Architecture & Directory Mapping Diagram

```mermaid
graph TD
    subgraph Suite ["Tests Directory (tests/)"]
        Unit["tests/unit/\n(Pure Python, fake adapters, no I/O)"]
        Integ["tests/integration/\n(App boot, real DB, cross-layer flows)"]
        Fixtures["tests/fixtures/\n(Shared builders, golden corpora, envelopes)"]
        Conf["tests/conftest.py\n(SSL_CERT_FILE guard, deselect banner)"]
    end

    subgraph Mirror ["Source Code Mirroring (src/cowork_agent/)"]
        Unit --> U1["tests/unit/domain/ ↔ domain/"]
        Unit --> U2["tests/unit/features/ ↔ features/"]
        Unit --> U3["tests/unit/integrations/ ↔ integrations/"]
        Unit --> U4["tests/unit/persistence/ ↔ persistence/"]
        Unit --> U5["tests/unit/orchestration/ ↔ orchestration/"]
        Unit --> U6["tests/unit/scripts/ ↔ scripts/ (in-process via cli_harness)"]
        Unit --> U7["tests/unit/fixtures/ (golden-fixture schema validation)"]

        Integ --> I1["tests/integration/api/ (FastAPI over ASGI transport)"]
        Integ --> I2["tests/integration/persistence/ (real Postgres, one cached probe)"]
        Integ --> I3["tests/integration/email_action_plan/ (Gmail → plan → persist)"]
        I1 --> L1["test_e2e_frontend_api.py\nmarked `live` — deselected by default,\nskips behind a loud banner"]
    end

    subgraph Commands ["Execution Commands (always `uv run`)"]
        C1["uv run pytest tests/unit/features -q\n(narrowest route for the change)"]
        C2["uv run pytest -q\n(full suite, ~19 s)"]
        C3["uv run ruff check . && uv run mypy src\n(quality gates)"]
    end
```

## Key Test Workflow & Folder Concepts

1. **Mirroring structure**: `tests/unit/` mirrors `src/cowork_agent/` 1-to-1, so
   the test file for any module is findable by path. `tests/README.md` §1 turns
   that mapping into named routes with measured costs.
2. **One invariant, one owner**: before writing a test, look up its invariant in
   `tests/README.md` §3. A second assertion of the same fact at another layer is
   a deletion candidate, not coverage.
3. **Verification rule** (see `AGENTS.md`):
   - Run the narrowest route while editing; widen only when it is green.
   - Run `ruff check` and `mypy` whenever `src/` changes.
   - Run the full suite before committing, or immediately when a shared contract
     (ports, schemas, migrations) changes.
4. **Never `python -m pytest`** — it picks up the Anaconda interpreter on this
   machine and fails with unrelated `ssl` errors.
