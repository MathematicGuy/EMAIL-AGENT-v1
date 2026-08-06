# Cowork Agent — Email to Action Plan

Single bounded context covering the product defined by
`docs/PRD-v1-Core-Email-and-RAG.md` and `docs/PRD-v2-Memory-Extension.md`.
This glossary is the canonical vocabulary for humans and coding agents; use
these exact terms in code, tests, docs, and conversation. The extended format
(relations, example dialogue, flagged ambiguities) lives in
`UBIQUITOUS_LANGUAGE.md` — keep the two in sync when a term changes.

## Language

### Actors and product surface

**User**:
The knowledge worker whose Gmail is processed and whose tasks and memories are
tenant- and user-scoped.
_Avoid_: customer, account, owner

**Verified Principal**:
The authenticated tenant + user identity that scopes every Gmail, RAG, task,
and memory operation; never inferred from caller-provided query parameters.
_Avoid_: current user, caller identity

**Cowork**:
The product surface where generated tasks and memory controls are shown.
_Avoid_: dashboard, app UI, frontend (for the product concept)

### Execution

**@Email**:
The manual invocation that creates one Run; the only product entry path in
PRD-v1 and PRD-v2.
_Avoid_: digest trigger, scan button, sync

**Run**:
One execution of the Email Action Plan workflow over a mailbox slice, from
fetch through cleanup.
_Avoid_: digest (legacy API path only), job, scan, session

**Mailbox Connection**:
A user's read-only Gmail OAuth grant, durably stored with an encrypted refresh
token.
_Avoid_: account, inbox binding, integration

**Ephemeral Envelope**:
The normalized form of one Gmail message that exists only as current-run state
and is deleted at run completion.
_Avoid_: email object, message DTO, email record

### Classification and routing

**Classifier**:
The structured LLM call that decides actionability and knowledge sufficiency;
it proposes but never owns the final route or plan.
_Avoid_: analyzer, triage model, extractor (legacy)

**Route Decision**:
The classifier's structured per-email output (actionability, sufficiency,
gaps, query, reason codes, confidence).
_Avoid_: classification result, prediction

**Route**:
The execution path resolved deterministically by Agent Core: `NO_ACTION`,
`DIRECT_PLAN`, or `RETRIEVE_RAG`.
_Avoid_: intent, category, decision (for the resolved path)

**Route Resolver**:
The pure deterministic function combining policy guards, the Route Decision,
and confidence into a Route.
_Avoid_: router, dispatcher

**Policy Guard**:
A deterministic rule that forces or strongly biases retrieval for policy,
governance, procedure, form, template, tax, or internal-term requests.
_Avoid_: rule engine, policy (alone), filter

**Task Candidate**:
The intermediate unit formed when deterministic correlation groups one or more
Route Decisions by thread/incident; each receives exactly one final generation
call unless routed `NO_ACTION`.
_Avoid_: draft task, grouped email, incident (unless meaning the correlation
key itself)

### Generation and output

**Agent Core**:
The application layer that orchestrates the workflow and owns final Action
Plan generation.
_Avoid_: orchestrator service, brain, agent (alone)

**Generator**:
The final Action Plan generation LLM call, invoked exactly once per resolved
non-`NO_ACTION` Task Candidate.
_Avoid_: planner, synthesizer, pass-two

**Action Plan**:
The ordered steps produced by the Generator for one task.
_Avoid_: plan steps, todo list, workflow

**Task**:
The minimal durable artifact persisted per actionable result: title, summary,
plan, Gmail pointer, citations, missing information.
_Avoid_: action item (legacy compatibility shape only), todo, work item

**Citation**:
A reference to one retrieved company document chunk that supports a
company-grounded plan step.
_Avoid_: source, reference, evidence (for the document reference)

**Partial Plan**:
A plan generated in partial mode when retrieval fails or returns nothing, with
explicit missing information instead of invented procedures.
_Avoid_: degraded plan, fallback plan

### Memory

**Short-Term Memory**:
Ephemeral current-run state, including raw email, cleared at run completion.
_Avoid_: scratchpad, run cache

**Long-Term Memory**:
Explicit, user-configured preferences and configuration, loaded as a compact
profile during runs.
_Avoid_: user settings store, declarative store (in product language)

**Episodic Memory**:
Derived task history with validation lifecycle and retrieval eligibility.
_Avoid_: task history (when meaning the memory capability), memory log

**Semantic Memory**:
Company knowledge retrieved read-only through the RAG module.
_Avoid_: knowledge base (when meaning the memory type), company RAG (for the
memory-type concept)

**Memory Gateway**:
The logical facade centralizing namespace, eligibility, provenance, retention,
and deletion policy for all memory access; may be in-process.
_Avoid_: memory service, memory API

**Episode**:
One episodic record derived from a persisted task; written as
`system_generated` and retrieval-ineligible until validated.
_Avoid_: memory item, history entry

**Validation Status**:
An episode's lifecycle state: `system_generated`, `user_approved`,
`completed`, or `rejected`.
_Avoid_: validation_state, verification status, episode status

**Retrieval Eligibility**:
The code-enforced rule that only `user_approved` or `completed` episodes may
be retrieved; never prompt-enforced.
_Avoid_: eligibility (alone), trust flag

**Profile**:
The compact, bounded long-term preference set loaded before classification or
generation.
_Avoid_: user context, persona

**Provenance**:
Mandatory source, model, version, and lifecycle metadata on every durable
memory record.
_Avoid_: lineage, metadata (alone)
