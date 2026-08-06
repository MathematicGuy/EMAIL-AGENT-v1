# Ubiquitous Language

> Cowork Agent — Email to Action Plan (PRD-v1 + PRD-v2).
> **Canonical glossary:** `CONTEXT.md` at the repo root is the single source
> of truth for term definitions and is what coding agents read. This file adds
> relationships, example dialogue, and flagged ambiguities. When a term
> changes, update `CONTEXT.md` first, then this file.

## Actors and product surface

| Term | Definition | Aliases to avoid |
|---|---|---|
| **User** | The knowledge worker whose Gmail is processed and whose tasks/memories are scoped | customer, account, owner |
| **Verified Principal** | Authenticated tenant + user identity scoping every operation | current user, caller identity |
| **Cowork** | The product surface where tasks and memory controls are shown | dashboard, app UI |

## Execution

| Term | Definition | Aliases to avoid |
|---|---|---|
| **@Email** | The manual invocation that creates one Run; the only entry path | digest trigger, scan, sync |
| **Run** | One execution of the Email Action Plan workflow over a mailbox slice | digest (legacy path only), job, scan |
| **Mailbox Connection** | A read-only Gmail OAuth grant with encrypted refresh token | account, integration |
| **Ephemeral Envelope** | One normalized Gmail message existing only as current-run state | email object, message DTO |
| **Agent Core** | The application layer owning orchestration and final generation | orchestrator, brain |

## Classification and routing

| Term | Definition | Aliases to avoid |
|---|---|---|
| **Classifier** | Structured LLM call deciding actionability and knowledge sufficiency | analyzer, extractor (legacy) |
| **Route Decision** | The classifier's structured per-email output | classification result, prediction |
| **Route** | Deterministically resolved path: `NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG` | intent, category |
| **Route Resolver** | Pure function combining guards, Route Decision, and confidence into a Route | router, dispatcher |
| **Policy Guard** | Deterministic rule forcing/biasing retrieval for company-knowledge requests | rule engine, filter |
| **Task Candidate** | Correlated group of Route Decisions receiving at most one generation call | draft task, grouped email |

## Generation and output

| Term | Definition | Aliases to avoid |
|---|---|---|
| **Generator** | Final Action Plan generation call, once per resolved non-`NO_ACTION` Task Candidate | planner, pass-two |
| **Action Plan** | Ordered steps produced for one task | todo list, workflow |
| **Task** | Minimal durable artifact: title, summary, plan, Gmail pointer, citations | action item (legacy shape only), todo |
| **Citation** | Reference to a retrieved company chunk supporting a grounded step | source, evidence |
| **Partial Plan** | Plan in partial mode with explicit missing information, never invented procedures | degraded plan, fallback plan |

## Memory

| Term | Definition | Aliases to avoid |
|---|---|---|
| **Short-Term Memory** | Ephemeral run state including raw email, cleared at completion | scratchpad |
| **Long-Term Memory** | Explicit user-configured preferences, loaded as a compact profile | settings store |
| **Episodic Memory** | Derived task history with lifecycle and retrieval eligibility | task history (capability sense) |
| **Semantic Memory** | Company knowledge retrieved read-only via RAG | knowledge base |
| **Memory Gateway** | Logical facade enforcing namespace, eligibility, provenance, deletion | memory service |
| **Episode** | One task-derived episodic record, ineligible until validated | memory item |
| **Validation Status** | `system_generated` \| `user_approved` \| `completed` \| `rejected` | validation_state, verification status |
| **Retrieval Eligibility** | Code-enforced rule: only approved/completed episodes are retrievable | trust flag |
| **Profile** | Compact bounded preference set loaded before classification/generation | persona, user context |
| **Provenance** | Mandatory source/model/version/lifecycle metadata on durable records | lineage |

## Relationships

- A **User** owns one or more **Mailbox Connections**.
- An **@Email** invocation creates exactly one **Run** against one **Mailbox Connection**.
- A **Run** reads zero or more messages, each normalized into an **Ephemeral Envelope**.
- Every selected message receives exactly one **Route Decision** from the **Classifier**.
- Deterministic correlation groups Route Decisions into **Task Candidates** (one or more messages per candidate).
- The **Route Resolver** assigns exactly one **Route** per Task Candidate.
- A `RETRIEVE_RAG` Task Candidate performs zero or one retrieval; the **Generator** is then called exactly once.
- A `DIRECT_PLAN` Task Candidate skips retrieval; the Generator is called exactly once.
- A `NO_ACTION` Task Candidate produces no **Task** and no Generator call.
- A persisted **Task** carries zero or more **Citations** (only from the current retrieval) and a Gmail pointer.
- Every persisted Task writes exactly one **Episode** (`system_generated`, ineligible).
- An **Episode** becomes retrieval-eligible only through the `user_approved` or `completed` **Validation Status**.
- The **Memory Gateway** mediates all reads/writes to Short-Term, Long-Term, Episodic, and Semantic memory; Agent Core never bypasses it.
- A **Profile** is the read projection of Long-Term Memory; raw email never enters Long-Term, Episodic, or Semantic memory.

## Example dialogue

> **Dev:** "When a **Run** finishes, do we store the emails so the **Task** list can show them later?"

> **Domain expert:** "No. Emails live only in **Short-Term Memory** as **Ephemeral Envelopes** and are deleted at run completion. The **Task** persists the derived artifact — title, summary, plan — plus a Gmail pointer back to the source."

> **Dev:** "And if the **Classifier** says an email needs company policy, we retrieve and regenerate?"

> **Domain expert:** "Not regenerate. The **Route Resolver** picks `RETRIEVE_RAG`, we retrieve once, then the **Generator** runs exactly once with that context. There is never a pre-retrieval draft to fall back on — if retrieval fails we ship a **Partial Plan** with explicit missing information."

> **Dev:** "So the **Episode** we write for that task can be reused in the next similar run?"

> **Domain expert:** "Not yet. New episodes are `system_generated` and retrieval-ineligible. Only after the user approves or completes the task does **Retrieval Eligibility** flip — and that rule is enforced in the **Memory Gateway**, not in the prompt."

## Flagged ambiguities

- **"Action item" vs "Task"** — the current codebase and legacy API result shape use `actionItems`; the PRDs define the durable artifact as a **Task**. Canonical: **Task**. `actionItems` survives only as the versioned compatibility mapper output (master-comparison §7, compatibility contract); never name new code after it.
- **"Digest" vs "Run"** — code uses `DigestRun`, `DigestWorker`, `CreateDigestRun`, and the `/v1/mail-todo/runs` path. Canonical product term: **Run**. "Digest" is retained only in existing identifiers until a migration renames them; do not introduce new "digest" names.
- **"Extraction/extractor" vs "Classifier/Generator"** — the combined `ActionExtractorPort` predates the split. Canonical: **Classifier** and **Generator** as separate ports. "Extraction" should not appear in new code except the temporary compatibility adapter.
- **"validation_status" vs "verification"** — PRD-v1's task contract uses `validation_status: system_generated`; PRD-v2's episode lifecycle uses the same field name with three more states. Canonical: one **Validation Status** field, same enum, on both Task and Episode. Do not introduce `verification_status`.
- **"confidence"** — ambiguous between **classifier confidence** (numeric, part of the Route Decision) and **generation confidence** (nullable, on the Task). Always qualify: `classifier_confidence` / `generation_confidence`.
- **"Cowork" vs "Cowork Agent"** — **Cowork** is the product surface the user sees; **Cowork Agent** (Agent Core) is the system. Do not use "Cowork" to mean the backend.
- **"Incident"** — used only for the correlation key (`incident_key`) grouping related messages into one **Task Candidate**; not a domain object of its own.
