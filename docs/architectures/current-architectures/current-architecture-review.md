# Current Architecture — Adversarial Review Record

> **Historical snapshot notice (2026-08-09):** This review remains an evidence record for commit
> `cf2fd49801d5932b26de82af9d104d730cf58271`. Its findings that RAG/BM25/reranking were absent must
> not be read as current worktree status. See
> [`../../references/EMAIL-RAG-ARCHITECHTURE.md`](../../references/EMAIL-RAG-ARCHITECHTURE.md) and
> [`../master-comparison.md`](../master-comparison.md) for the implemented local V1-M3 retrieval
> slice and remaining target-only work.

## Scope

Adversarial correctness review of the three current-architecture documents against live source at commit `cf2fd49801d5932b26de82af9d104d730cf58271` on branch `main`, performed 2026-08-07.

Documents reviewed:

- `current-email-architecture.md` against `../extract-email.md`
- `current-rag-architecture.md` against `../extract-rag.md`
- `current-overall-architecture.md` against `../extract-overall-architecture.md`

Authority for "current" behavior was restricted to code actually wired by `create_app()`. `docs/references/ARCHITECHTURE.md` and `src/cowork_agent/persistence/migrations/001_mail_todo.sql` were treated as non-authoritative for runtime claims.

Method: an independent Claude pass over the contracts and live source first, then a bounded read-only second opinion from Codex (`codex exec --sandbox read-only`). Every Codex finding was re-verified against live source before any edit; none were accepted on assertion alone.

## 1. The decisive question: where is the Action Plan produced?

`../README.md` calls this the most important extraction question. All three documents originally understated the answer, and all three cited only `gemini.py:26-152` — omitting roughly 150 lines where the real shaping happens.

Verified chain:

| Stage | Location | What it does |
|---|---|---|
| Prompt construction | `src/cowork_agent/integrations/llm/providers/gemini.py` `_build_prompt` | Wraps untrusted email content, batches threads |
| Candidate authoring | External Gemini / Groq API | The LLM proposes `actionPlan` steps |
| JSON → contract | `src/cowork_agent/integrations/llm/providers/gemini.py:366-419` | The **adapter**, not the provider, builds `ExtractionBatch` |
| Sanitize and cap | `src/cowork_agent/integrations/llm/providers/gemini.py:431-466` | Drops empty, >600-char, case-folded duplicate, and prompt-leak steps; truncates to 5; renumbers |
| Correlated rebuild | `src/cowork_agent/integrations/llm/providers/gemini.py:469-535`, `:548-570` | Groups actions by normalized `incidentKey`; any group of 2+ actions is merged and its plan **rebuilt** by interleaving member plans |
| Final assignment | `src/cowork_agent/features/email_action_plan/workflow.py:218` | `DigestWorker` copies the tuple **unchanged** into `ActionItem.action_plan` |

Answer of record: **the Email workflow owns it, and within that workflow ownership is split between the LLM and the provider adapter. `DigestWorker` orchestrates but does not author or edit steps.** No RAG module participates; none exists.

Two sub-points that were initially stated too narrowly and are now corrected:

- Merging is **not** limited to groups spanning multiple emails. Two actions in the *same* email sharing an `incidentKey` are merged (`src/cowork_agent/integrations/llm/providers/gemini.py:481-490`).
- `_select_merged_steps` dedupes on the exact `(instruction, basis)` pair and stops at 5. Identical instructions with different bases both survive, and no action is guaranteed to contribute a step.

Related: `src/cowork_agent/integrations/llm/providers/groq.py:14-21` imports the schema, system instruction, batching, prompt builder, parser, and merge helpers directly from `gemini.py`. The two adapters are not independent implementations — only transport and error mapping differ. Any change to plan shaping affects both providers.

## 2. Findings accepted and corrected

### Email document

| # | Defect | Correction |
|---|---|---|
| E1 | `flowchart TD` violated the contract's required `LR` | Changed to `flowchart LR` |
| E2 | Diagram omitted `InMemoryQueue` | Added `QUEUE` node, labelled "run ID registry, no consumer" |
| E3 | Diagram showed `CREATE --> BG`, implying `CreateDigestRun` dispatches the worker | Corrected to `API --> BG --> WORKER`; the route dispatches, not the service (`src/cowork_agent/app.py:236`) |
| E4 | Raw-content node was orphaned and implied raw email is persisted | Relabelled to state raw email never enters persistence; the misleading solid edge was removed |
| E5 | `GmailMailboxAdapter` credited with building `ThreadContext` | Split: the adapter builds `EmailEnvelope` (`src/cowork_agent/integrations/gmail/provider.py:204-212,277-298`); `DigestWorker` assembles `ThreadContext` (`src/cowork_agent/features/email_action_plan/workflow.py:125-159`) |
| E6 | `SafeTextAttachmentExtractor` credited with recording warnings and setting partial status | Reassigned to `DigestWorker` (`src/cowork_agent/features/email_action_plan/workflow.py:133-158`, `:284-294`); the extractor only returns a `warning_code` or raises |
| E7 | Diagram showed rate-limit failover with no retry edge, though the contract requires retries be shown | Added `FAILOVER -- retry with next configured key --> ACTION` (`src/cowork_agent/integrations/llm/providers/gemini.py:136-152`) |
| E8 | OAuth path omitted the Gmail profile call used to identify the mailbox | Added `PROFILE` (`users.getProfile`) between token exchange and persistence (`src/cowork_agent/integrations/gmail/provider.py:76-85`, `:268-270`) |
| E9 | §5 omitted four endpoint response shapes, though the contract requires every returned field | Added `/health`, OAuth connect/callback, connection list, and disconnect, including all `_public_connection` fields |
| E10 | Scope check described as verifying what Google granted | Corrected: it is exact tuple equality and falls back to the configured value when the response carries no scopes, so it cannot fail in that case (`src/cowork_agent/integrations/gmail/provider.py:85`, `:133-134`) |
| E11 | OAuth pending state described as retained only until TTL | Corrected: invalid at TTL but evicted only by a later consume sweep, its own consumption, or restart (`src/cowork_agent/integrations/gmail/auth.py:81-93`) |
| E12 | Idempotency described without stating what is not checked | Added: replay is keyed on `(user_id, idempotency_key)` with no payload-equivalence check |
| E13 | 503 on `POST /runs` attributed to the run pipeline | Corrected: it reports Gemini configuration failure specifically (`src/cowork_agent/app.py:223-227`) |
| E14 | Source evidence understated the Gemini range | Added `:366-419`, `:431-466`, `:469-535`, `:548-570` and the `src/cowork_agent/integrations/llm/providers/groq.py:14-21` coupling |

### RAG document

| # | Defect | Correction |
|---|---|---|
| R1 | Diagram B placed the Email `POST /runs` endpoint inside `CALLER` | Moved to a distinct `EMAIL WORKFLOW API, outside any RAG boundary` subgraph; `RETRIEVAL API` now holds only "No RAG retrieval endpoint". All six contract-required subgraphs remain present |
| R2 | "The provider returns structured `ExtractionBatch` data" | Corrected: the provider returns raw JSON; the adapter builds `ExtractionBatch`. Final network calls named (`GoogleGenAITransport.generate_content`; Groq `urllib` POST) |
| R3 | Generation stage collapsed worker, adapter, and external API into one node | Split into `WORKER`, `ADAPTER`, `PROVIDER`, `SHAPE`, `PLAN` |
| R4 | Attachment flow described as a "bounded download" | Corrected: the full response is fetched and fully base64-decoded, *then* size-checked; only extraction is bounded (`src/cowork_agent/integrations/gmail/provider.py:223-238`) |
| R5 | Source evidence labelled worker code as "Action Plan creation" | Relabelled as orchestration and final `ActionItem` construction; adapter ranges added |

### Overall document

| # | Defect | Correction |
|---|---|---|
| O1 | "`DigestWorker` constructs final `ActionItem.action_plan` values" — directly contradicted the other two documents | Corrected to "assigns the adapter-shaped `action_plan` unchanged" |
| O2 | Queue enqueue described unconditionally | Corrected: only on first creation, not on idempotent replay |
| O3 | §4 merge description said "whenever several emails share an `incidentKey`" | Corrected per §1 above |
| O4 | Gemini retry controls overstated | Added the `min(GEMINI_MAX_ATTEMPTS_PER_REQUEST, key count)` cap, the single-key no-retry consequence, and the `GEMINI_ROTATE_ON_RATE_LIMIT=false` bypass |
| O5 | Groq row implied an SDK | Corrected: raw `urllib`, no Groq SDK dependency |
| O6 | Adapters presented as independent | Added the `src/cowork_agent/integrations/llm/providers/groq.py` → `src/cowork_agent/integrations/llm/providers/gemini.py` import coupling |
| O7 | Schema-validation failure row was wrong | Corrected: Groq raises `GROQ_API_ERROR`; Gemini raises a bare `ValueError` that surfaces as generic `RUN_PROCESSING_FAILED`. Added a provider-misconfiguration row |
| O8 | "The only explicit application event is the exception log" | Corrected to "log statement"; `DigestCompletedEvent` is emitted to the outbox on every run but nothing consumes it (`src/cowork_agent/features/email_action_plan/workflow.py:243`) |
| O9 | Worker credited with normalizing email/thread context | Split per E5 |
| O10 | Scope and OAuth-state claims | Same corrections as E10 and E11 |
| O11 | Source evidence understated adapter ranges | Added `src/cowork_agent/integrations/llm/providers/gemini.py:366-419` and `src/cowork_agent/integrations/llm/providers/groq.py:14-21` |

## 3. Findings not accepted as stated

- **Move the raw-content node out of `STORAGE` entirely.** The contract explicitly requires the diagram to "show where raw email enters or leaves persistence". Removing the node would drop that answer. Resolution: the node stays inside `STORAGE` but now states the absence outright, and the solid edge that implied a write was removed. This matches the pattern already used in RAG Diagram A, where absence nodes carry no arrows.

- **Historical claim that a prior Codex run had produced findings.** The first Codex invocation died with no output; root cause was `SSL_CERT_FILE` pointing at a nonexistent CA bundle, which flooded the MCP transport with certificate errors. Nothing from that run was used. The accepted second opinion came from a clean re-run with MCP servers disabled.

## 4. Open questions for human review

These could not be resolved from this checkout and should be settled before the master comparison:

1. **Is "RAG not yet implemented" the intended conclusion?** No ingestion, retrieval, index, reranker, citation validator, or knowledge store exists in `src/`, and `pyproject.toml` carries no vector/search dependencies. `docs/references/ARCHITECHTURE.md` describes a `knowledge/` package, Qdrant, hybrid retrieval, and Langfuse. Either that document describes a target system, or a RAG implementation lives on a branch or repository not present here.
2. **Intended owner of the Action Plan going forward.** Shaping currently lives in the provider adapter, duplicated across providers only by import. If retrieval is added later, whether plan construction moves into an agent or planning service is an open design decision, not a fact recoverable from code.
3. **`InMemoryQueue` and `InMemoryOutbox` have no consumer.** Whether they are placeholders for a broker and an event publisher, or dead code, is not determinable from source.
4. **`MailTodoApi` in `src/cowork_agent/api/handlers.py` is unwired** — `app.py` imports only `_jsonable` from it. Intent unknown.
5. **Tenant identity is caller-asserted.** `user_id` is a query parameter with no session or JWT binding. Whether an external auth layer is assumed is not visible in this repository.
6. **PostgreSQL schema in `src/cowork_agent/persistence/migrations/001_mail_todo.sql` is not wired.** Whether it is the migration target or belongs to a different runtime is unresolved.

## 5. Verification performed

| Check | Result |
|---|---|
| Required contract sections present in all three documents | Pass |
| Code-fence balance | Pass (4 / 10 / 4 fences) |
| Mermaid orientation vs contract | Pass — Email `LR`, RAG two × `LR`, Overall `TB` |
| Required bounded subgraph names present | Pass in all four diagrams |
| All four diagrams render with `mmdc` from a clean state | Pass — mermaid-cli 11.16.0 |
| Every `src/...py:line` citation resolves and is in range | Pass |
| Cross-document consistency on Action Plan ownership, dispatch, and queue semantics | Pass |
| Path-update re-verification against live `src/cowork_agent/` code | Pass (2026-08-07) |
| `python -m pytest tests/unit/features/email_action_plan/test_policies.py -q` | 7 passed |
| Working tree limited to task-owned documentation changes | Pass |

### Limits

- `mmdc` requires `--no-sandbox` Puppeteer flags in this environment; Chrome cannot launch otherwise. Rendering proves the diagrams are syntactically valid, not that the layout is optimal — the Email `LR` diagram is dense with long crossing edges.
- The Gmail, pipeline, and server test modules could not be collected: `google_auth_oauthlib` is not installed. Dependencies were **not** installed, so runtime suite success is not claimed or inferred.
- No document claim in these files depends on the uncollected tests; all were verified by direct source reading instead.
- No application source code was changed. No staging, commit, push, or dependency installation occurred. The pre-existing dirty state (modified `.gitignore`; untracked `.claude/`, `docs/architectures/`, `docs/references/`) is preserved.
- `master-comparison-architecture.md` was **not** started, and no redesign of the target system was performed.
