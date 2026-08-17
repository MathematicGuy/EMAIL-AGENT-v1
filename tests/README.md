# Test Routing Index

A map for picking the **smallest** test route that covers a change, and a
registry of which file owns which invariant so no one writes a test that
already exists.

Always `uv run pytest`. Plain `python -m pytest` picks up the Anaconda
interpreter on this machine and fails with unrelated `ssl` errors.

**Whole suite: `uv run pytest -q` -> ~24 s, 1311 passed.** Defaults live in
`pyproject.toml`: `-n 4 --dist loadfile -m 'not live' --strict-markers`.

The suite is **offline by construction** -- see §7. Do not undo that to make a
test pass.

---

## 1. Route Index

Pick the narrowest row that contains your change. Times are **serial** (`-n0`),
which is what one route costs; the full suite is parallel.

| # | Route | Tests | Serial | Covers |
|---|---|---|---|---|
| R1 | `tests/unit/domain` | 172 | 0.7 s | Frozen contracts, enums, validation rules. No I/O. |
| R2 | `tests/unit/features` | 634 | 2.0 s | Chat controller/memory/intent + email action-plan mapping. Fakes only. |
| R3 | `tests/unit/integrations/rag` | 73 | 5.5 s | BM25, RRF fusion, reranker, query guard, in-repo memory. |
| R4 | `tests/unit/integrations/llm` | 43 | 1.6 s | Prompt assembly, parsing, key rotation, classifiers. |
| R5 | `tests/unit/integrations/gmail` | 19 | 0.8 s | OAuth/PKCE, token cipher, mailbox adapter. |
| R6 | `tests/unit/integrations` | 251 | 9.0 s | R3+R4+R5 plus bootstrap, Supabase. |
| R7 | `tests/unit/persistence` | 19 | 1.3 s | Repository logic against fakes. |
| R8 | `tests/unit/orchestration` | 12 | 1.5 s | Workers, pollers, recovery. |
| R9 | `tests/unit/scripts` | 66 | 10.4 s | `scripts/*.py` eval CLIs. **Slowest unit route.** |
| R10 | `tests/unit/fixtures` | 33 | 2.2 s | Golden-fixture schema and corpus-label validation. |
| R11 | `tests/integration/api` | 25 | 5.5 s | FastAPI via in-process ASGI transport. |
| R12 | `tests/integration/persistence` | 9 | 3.9 s | Real PostgreSQL. **Skips wholesale without a server.** |
| R13 | `tests/integration/email_action_plan` | 45 | 3.3 s | Gmail -> classify -> plan -> persist, end to end on fakes. |
| R14 | `tests/integration` | 76 | 14.4 s | R11+R12+R13 plus corpus-backed workflow. |
| R15 | `tests/unit` | 1244 | 29.3 s | Everything above the integration line. |
| R16 | `tests/unit --ignore=tests/unit/scripts` | 1178 | 19 s | R15 minus the eval CLIs. Good default when `scripts/` is untouched. |
| — | *(everything)* | 1311 | **24 s parallel** | `uv run pytest -q` |

### Source -> route

| Edited under `src/cowork_agent/` | Run |
|---|---|
| `domain/` | R1, then R2 |
| `features/ai_chat/` | R2 |
| `features/email_action_plan/` | R2 + R13 |
| `integrations/rag/` | R3 (+ R6 if `bootstrap.py` or `project_documents.py`) |
| `integrations/knowledge_ingestion/` | `tests/unit/integrations/knowledge_ingestion`, then `test_rag.py` if `load_corpus` changed |
| `integrations/llm/` | R4 |
| `integrations/gmail/` | R5 + R13 |
| `persistence/` | R7 + R12 |
| `orchestration/` | R8 |
| `app.py`, API routes | R11 |
| `identity.py`, session/cookie | R11 + `tests/unit/test_identity.py` |
| `scripts/*.py` | R9 |
| `data/extracted/*.md` (corpus) | R10 + R3 |

---

## 2. Markers

Registered in `pyproject.toml`; `--strict-markers` rejects anything else.

| Marker | Meaning | Default |
|---|---|---|
| `live` | Needs a real external process or credentials. | **Deselected.** `-m live` to opt in. |
| `slow` | >1 s of wall clock on its own. | Selected (nothing relies on it yet). |
| `serial` | Must not run under xdist — spawns processes or binds a fixed port. | Selected; keep it on one worker. |

Every run ends with a yellow **`DESELECTED - NOT VERIFIED BY THIS RUN`** banner
naming what the filter dropped. A green summary with that banner above it is
*not* a fully verified suite.

`tests/integration/api/test_e2e_frontend_api.py` is the only `live` module (24
tests). It needs a real `mail-todo-api` subprocess plus completed Gmail OAuth.
When the server will not boot it **skips behind a wall of `!!!!` explaining why**
— it never errors, so it can never mask a real failure.

---

## 3. Invariant Ownership

**Before writing a test, find its invariant here.** If a row already exists, add
a case to the owning file instead of starting a new one. If the invariant is
absent, add the row when you add the test.

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Legacy `/result` JSON key set, `nextActions` slice, empty-state message, item ordering | `unit/features/email_action_plan/test_compat_mapper.py` | any API-level test |
| `processedEmails` is development-only | `integration/api/test_principal_boundary.py` | — |
| Run creation is idempotent per `(user, Idempotency-Key)` | `integration/email_action_plan/test_workflow.py` | API tests (they get it transitively) |
| Persisted tasks survive a replayed run without duplicating | `integration/email_action_plan/test_workflow.py` | — |
| Postgres migrations apply once and are idempotent | `integration/persistence/test_postgres_repositories.py` | — |
| No raw email body reaches any API response | `integration/api/test_principal_boundary.py` | workflow/repository tests |
| No raw email body reaches chat memory | `unit/domain/test_chat_contracts.py` | gateway tests |
| Retrieval ordering, `top_k`, `min_score`, timeout status | `unit/integrations/rag/test_rag.py` (in-repo) + `unit/integrations/rag/test_turbovec_memory.py` | integration tests |
| Binary `document_date` harvest (PDF `/Info`, DOCX props; never mtime) | `unit/integrations/knowledge_ingestion/test_date_harvest.py` | service tests except one wire-up |
| Company RAG pre-filter (`document_ids` / `years` / `months`); missing date fails year/month | `unit/integrations/rag/test_rag.py` (`allowed_chunk_indices`) | hybrid/turbovec except one empty-allowlist-no-embed |
| Retrieval over the *committed corpus* + degrade-to-null path | `unit/integrations/test_bootstrap.py` | — |
| Jina embed key rotation (429, empty-wallet 403; not generic 403) | `unit/integrations/rag/test_embeddings.py` | bootstrap / hybrid |
| Project-document ACL (six SQL conditions before embed) + cross-project isolation + empty-allowlist short-circuit | `unit/integrations/test_project_documents_hybrid.py` | orchestration/API tests |
| Eval report is metadata-only (no query/answer/chunk text) | one test per script in `unit/scripts/` | — |
| OAuth grant identity binding (resolver decides `user_id`) | `unit/integrations/gmail/test_provider.py` | — |
| Broken `SSL_CERT_FILE` cannot poison a run | `tests/conftest.py` | — |
| GET `/sessions/{id}/messages` omits `rag_evidence.content` unless `include_content=true` | `integration/api/test_chat_api.py` | frontend mapper except one preview-only case |
| No test outside the `live` tier opens a non-loopback socket | `tests/unit/test_network_guard.py` | — |
| App boot never reaches the embedding API (`RAG_STORE_PROVIDER` pinned) | `tests/conftest.py` | API/workflow tests |

### Two facts that break tests if you forget them

- **`HashingEmbedder` carries no semantics.** It buckets tokens by hash. Never
  assert *which* document ranks first under it — only counts, ordering by score,
  thresholds, and status codes.
- **`tenant_id` is gone from the retrieval/email contracts** (single-user app).
  It still exists on `VerifiedPrincipal` and the chat-memory schema. Do not add
  it to `KnowledgeChunk`, `SemanticRetrievalRequest/Response`,
  `EphemeralEmailEnvelope`, `GenerationContext`, or `load_corpus`.

---

## 4. Rules for Adding Tests

1. **One invariant, one owner.** Check §3 first. A second assertion of the same
   fact at a different layer is a deletion candidate, not coverage.
2. **Test at the lowest layer that can observe the behaviour.** The retired
   `tests/compatibility/` suite booted a FastAPI app across 627 lines to exercise
   three pure functions; `test_compat_mapper.py` does it in 15 tests and 0.8 s.
3. **No subprocess for CLI assertions.** Use
   `tests/unit/scripts/cli_harness.py::run_cli`, which calls `main(argv)`
   in-process with stdio captured. Keep exactly **one** subprocess test per
   script (`test_help_runs_without_provider_keys`) to prove the entry point is
   executable. This alone took `unit/scripts` from 40 s to 13 s.
4. **Probe an external service once.** See
   `tests/integration/persistence/pg_probe.py`. Nine modules each opening their
   own 3 s connection cost 19 s per run to learn the same thing.
5. **A missing dependency skips loudly; it never errors.** Errors are for
   regressions. Print a banner that says what did not run and how to run it.
6. **Name the behaviour, not the mechanism.**
   `test_min_score_excludes_everything_below_the_threshold`, not
   `test_min_score_2`.
7. **New external dependency? Mark it `live` and `serial`.**

### Pruning checklist

Delete a test when any of these holds:

- Its invariant already has an owner in §3 and this is not the owner.
- It asserts a field that no longer exists on the contract (grep `src/` first —
  a stale *kwarg* means fix the call, a stale *purpose* means delete the test).
- It re-tests framework behaviour (pydantic validation, FastAPI routing).
- It only passes because a broad `except Exception` swallowed the real error.

---

## 5. Route Optimization

Do **not** open with the full suite. The sequence:

```bash
# 1. Narrowest route from §1 (seconds).
uv run pytest tests/unit/integrations/rag -q

# 2. Widen one level only if step 1 passes.
uv run pytest tests/unit/integrations -q

# 3. Full suite once, at the end.
uv run pytest -q
```

### Avoiding repeated work

| Goal | Flag |
|---|---|
| Re-run only what failed last time | `--lf` |
| Failed first, then the rest | `--ff` |
| See what a route *would* run, without running it | `--collect-only -q` |
| Count only | `--collect-only -q \| tail -1` |
| Find the next thing worth optimizing | `--durations=15` |
| Stop at the first failure | `-x` |
| Keep tracebacks cheap in context | `--tb=line` or `--tb=short` |

`--lf` and `--ff` read `.pytest_cache`. If you see
`PytestCacheWarning: cache could not write path`, the cache is stale-locked and
those flags silently degrade to running everything — clear `.pytest_cache/` or
add `-p no:cacheprovider` and select explicitly instead.

### Token-cheap defaults

`-q --tb=line --no-header`. A green route prints one line. Add `-x` when you
expect a failure so only the first traceback lands in context.

---

## 6. Layout

```
tests/
  conftest.py                       suite guards: SSL_CERT_FILE, offline RAG pin,
                                    outbound-socket block, deselect banner, log quiet
  unit/test_network_guard.py        proves the socket block actually blocks
  fixtures/                         shared builders and golden-fixture loaders
  unit/                             no I/O, no app boot, fakes only
    scripts/cli_harness.py          in-process runner for scripts/*.py
  integration/
    api/                            FastAPI over in-process ASGI transport
    api/test_e2e_frontend_api.py    the only `live` module (real subprocess)
    persistence/pg_probe.py         one cached Postgres reachability check
```

Gate before handing work back:

```bash
uv run pytest -q && uv run ruff check . && uv run mypy src
```

---

## 7. The Offline Guarantee

`tests/conftest.py` enforces two rules that keep a run's result a function of
the code and nothing else.

### `RAG_STORE_PROVIDER` is pinned to `none`

`build_semantic_memory` defaults to `turbovec`, which loads the whole committed
corpus and then calls the **real Jina embedding API** to build an index.
`create_app()` runs it during lifespan startup, so every test that booted the
API made live, billable HTTP calls on whatever keys sat in the developer's
`.env`.

It looked fine only because the call was 403ing and the `except` branch
degraded to `NullSemanticMemory` -- every assertion in the API tier was running
against the degraded object *by accident*. What the suite actually tested
depended on three things that have nothing to do with the code:

| Ambient state | What the tests then exercised | Cold-cache suite |
|---|---|---|
| Bad/absent key (403) | `NullSemanticMemory` via the degrade path | ~24 s |
| Working key, no snapshot | a real Jina-embedded index, built per boot | **~115-155 s** |
| Working key, snapshot on disk | an index loaded from `.data/turbovec_index.tvim` | ~16 s |

Three different objects under test, selected by a cache file and a wallet
balance. Pinning the disabled provider removes the variable entirely and takes
the cold-cache suite from ~2 minutes to ~22 s.

Set it explicitly (`RAG_STORE_PROVIDER=turbovec uv run pytest ...`) if you mean
to exercise the real store. To test the *bootstrap* rather than the network,
copy `unit/integrations/test_bootstrap.py`, which stubs `load_corpus` and
`JinaEmbeddingAdapter` and stays offline.

### Non-loopback sockets raise

The `_no_external_network` autouse fixture replaces `socket.socket.connect` for
the duration of each test. Loopback and any host named in `PG_TEST_URL` /
`DATABASE_URL` pass through -- the Postgres tier and the live server subprocess
need them. Everything else raises `RuntimeError: Blocked outbound connection`.

`live`-marked tests are exempt by definition: real Gmail OAuth and real Gemini
calls are what they exist to exercise.

If you hit the error, do not widen the allowlist. Stub the adapter at the seam,
or mark the test `live` and accept that it leaves the default selection.

### Adding a test that needs a service

1. Can it be a fake? Do that -- see `integrations/*/fakes.py`.
2. Needs the real thing? Mark it `live` (and `serial` if it binds a port or
   spawns a process), and give it a loud skip when the service is absent.
3. Never let a network failure degrade silently into a passing assertion. That
   is the exact bug this section exists to prevent.
