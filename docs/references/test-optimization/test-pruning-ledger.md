# Test pruning ledger (workstream B)

Evidence-backed candidates from `tests/README.md` §4. **No tests deleted.**
Task 6 is blocked until you mark rows.

Scratch sections (do not edit during review; this file is the merge):

| File | Task |
|---|---|
| [test-pruning-ledger-coverage.md](test-pruning-ledger-coverage.md) | 2 — coverage map |
| [test-pruning-ledger-dead.md](test-pruning-ledger-dead.md) | 3 — §4.2 stale contracts |
| [test-pruning-ledger-dup.md](test-pruning-ledger-dup.md) | 4 — §4.1 duplicate invariants |
| [test-pruning-ledger-framework.md](test-pruning-ledger-framework.md) | 5 — §4.3 framework re-tests |

Mark the **Status** column: `approved-delete` | `approved-fix` | `hold` | `approved-add-§3`.
Unmarked stays `pending`.

## Coverage map header (Task 2)

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Command | `uv run pytest --cov=cowork_agent --cov-context=test --cov-report=term-missing -n 0 -q` |
| xdist | off (`-n 0`) |
| Versions | pytest-cov 7.1.0, coverage.py 7.15.4 |
| Result | 1840 passed, 9 skipped, 267 deselected, 3 xfailed, 2 xpassed in 60.19s |
| Default suite | still uninstrumented (`addopts` unchanged) |

Zero-hit `src/` (missing tests, **not** deletions): `features/ai_chat/graph/{__init__,nodes,runner,state}.py`.

240 tests have no `cowork_agent` coverage context (mostly `tests/unit/scripts/*`, fixtures, migrations, harness). **Not** §4 evidence. See coverage scratch §2.

## Parent review of proposed deletes

Task 4 proposed two deletes. Adversarial review (degraded: nested reviewer used `ask_user_question` and was killed) found extra facts on both. They are **downgraded to keep (wire-up)** here; mark `approved-delete` only if you disagree.

| id | Task 4 said | Parent ruling | Why |
|---|---|---|---|
| I-001 | delete empty-state via `GetDigestResult` | **keep (wire-up)** + add §3 | Owner is pure mapper `_shape()`; candidate runs worker + query. Extra fact. |
| I-024 | delete hybrid `document_ids` retrieve | **keep (wire-up)** + add §3 | Owner is `allowed_chunk_indices` indices; candidate proves hybrid retrieve applies the filter (`SUCCESS` + `chunk_id`). Extra fact; deleting would let hybrid skip the pre-filter while R10 still passed. |

Task 3 deletes: **0**. Task 5 deletes: **0**.

## Rows that need a mark

| id | nodeid / cohort | §4 | proposed action | Status |
|---|---|---|---|---|
| D-001 | `tests/integration/test_knowledge_ingestion_to_rag.py::test_ingested_docx_markdown_is_loadable_by_rag` | 2 | **fix call** — drop unused `load_corpus(..., tenant_id="local")`. Do not delete. Ask before removing `knowledge_base.load_corpus` signature. | pending |
| D-002 | `tests/integration/email_action_plan/test_workflow.py::test_retrieve_rag_workflow_runs_end_to_end_over_in_repo_memory` | 2 | **fix call** — drop unused `load_corpus(..., tenant_id=LOCAL_TENANT_ID)`. Same src-signature ask. | pending |
| I-001 | `tests/integration/email_action_plan/test_workflow.py::test_result_has_explicit_empty_state_message` | 1 | **keep (wire-up)** (Task 4 wanted delete) | pending |
| I-024 | `tests/unit/integrations/rag/test_hybrid.py::test_hybrid_retrieve_with_document_ids_does_not_return_excluded` | 1 | **keep (wire-up)** (Task 4 wanted delete) | pending |
| §3-batch | ~29 duplicate-audit drafts + 4 HTTP 422 drafts | 1 / 3 | **add §3 row** — full text in dup + framework scratch files | pending |

`except Exception` / `except:` / `except BaseException` in tests: **none**. pydantic `ValidationError`: **none**. FastAPI 422 hits all pin app policy → keep.

## Counts (after parent review)

| Action | Count |
|---|---:|
| `delete` | **0** (2 Task-4 proposals downgraded) |
| `fix call` | 2 |
| `add §3 row` | ~33 drafts |
| `keep` | remainder (including 240 empty-context cohort, protected tests, owned qdrant degrade) |
| Tests deleted this round | **0** |

Zero deletions + new §3 rows is a successful B if you approve the add-row batch and the two fixes.

## Protected (never prune)

`test_network_guard.py`, `conftest.py`, `test_xdist_harness.py`, `test_pg_probe.py`, `live`, XPASS `q-006`/`q-014`, `tests/integration/persistence/*`.

## Out of this checkpoint

- Task 7 mutmut: skipped unless you ask.
- Do not commit `evaluations/MEMORIES/runs/memeval-chat.db`.
- **A+C dropped (2026-08-21):** testmon configure refuse + lazy `integrations.llm.observe` did not reduce default `uv run pytest -q` wall clock. Source restored to `from langfuse import observe`. Coverage extra (Task 1) and this ledger stay.
