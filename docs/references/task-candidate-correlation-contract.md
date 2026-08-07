# Task-Candidate Correlation Contract (Frozen)

**Status:** Frozen (Phase 0, P0-D) · **Date:** 2026-08-07
**Authority:** `docs/master-comparison.md` §7 "Target execution unit and call
cardinality" (source of truth); PRD-v1 §12.2–§12.4; `tasks/plan.md` Phase 0.
**Consumers:** V1-M2 (classifier split, correlation, resolver), V1-M3
(generator), V1-M4 (telemetry). Any change to this contract requires a
master-comparison update first.

This document formalizes — it does not change — the execution-unit and call-
cardinality decisions already recorded in master-comparison §7. All enum names
match master-comparison Step 6.

## 1. Pipeline unit definitions

| Unit | Definition | Provenance |
|---|---|---|
| Selected email | One fetched email chosen for classification in a Run | Gmail fetch stage (`EphemeralEmailEnvelope`) |
| `EmailRouteDecision` (6.2) | One schema-validated routing decision for one selected email | Classifier stage |
| Task Candidate | Application-owned deterministic grouping of one or more correlated decisions (same thread/incident) | Correlation stage |
| Resolved route | Task Candidate resolved once to `no_action`, `direct_plan`, or `retrieve_rag` | Route Resolver + policy guards (FR-06/FR-07) |
| `ActionPlanOutput` (6.6) | Final generated Task artifact | Generator stage |

## 2. Frozen cardinality rules

Numbered to match master-comparison §7 "Target execution unit and call
cardinality":

1. **Bounded classifier batches.** A Run may use one or more bounded classifier
   batch calls. Batch size is bounded by configuration, never by provider
   limits discovered at runtime. No email is classified more than once per Run
   (excluding the single documented repair/fallback retries of rule 2).
2. **One decision per selected email.** Every selected email receives exactly
   one schema-validated `EmailRouteDecision`. Schema validation failure allows
   exactly one repair retry; classifier failure falls back per PRD-v1 §12.2
   (retry once, then conservative `retrieve_rag`). Both paths still yield
   exactly one decision per email.
3. **Deterministic correlation.** Application-owned deterministic logic (no LLM
   call) correlates decisions by thread/incident and forms Task Candidates.
   Correlation must preserve, on every resulting Task and `ActionPlanOutput`:
   - `source_message_ids` — the complete set of Gmail message ids that
     contributed to the candidate (never truncated, never re-derived from
     summaries);
   - `incident_key` — stable grouping key, `null` only when the candidate is a
     single uncorrelated email;
   - fingerprint-based dedupe behavior frozen by the compatibility suite
     (`tests/compatibility/test_ordering_and_dedupe.py`).
   Same input decisions ⇒ same candidates, in the same order.
4. **One route per Task Candidate.** Each Task Candidate resolves exactly once
   to `no_action`, `direct_plan`, or `retrieve_rag`, via the pure deterministic
   Route Resolver with FR-07 policy guards. No re-resolution after retrieval or
   generation.
5. **Zero-or-one retrieval.** A `retrieve_rag` Task Candidate performs zero or
   one logical retrieval operation (through `SemanticMemoryPort`), with only
   the documented bounded technical retry (PRD-v1 §12.3: retry once, then
   structured empty result ⇒ Partial Plan). `direct_plan` and `no_action`
   candidates perform zero retrievals.
6. **Exactly one generator call per resolved non-`no_action` candidate.**
   Agent Core invokes the final generator exactly once per resolved
   `direct_plan` or `retrieve_rag` Task Candidate. It never invokes the
   generator for `no_action`. The single schema-repair retry of PRD-v1 §12.4
   is the only exception and still counts as one logical generation.

## 3. Observability obligations

Telemetry (TraceEvent 6.8; delivered in V1-M4) must report, separately per
Run:

- classifier batch count;
- email decision count (must equal selected-email count);
- correlated Task Candidate count;
- retrieval count (≤ `retrieve_rag` candidate count);
- generator count (must equal non-`no_action` candidate count, excluding
  repair retries, which are reported separately).

Divergence between these counts is a contract violation, not a warning.

## 4. Legacy-behavior preservation

- Multiple messages forming one correlated incident/action must keep
  `source_message_ids`, `incident_key`, evidence provenance, and dedupe
  behavior (master-comparison §7 "Compatibility contract").
- The compatibility suite (`tests/compatibility/`) must stay green through
  V1-M1..V1-M4; correlation changes are validated against it plus the labeled
  routing fixtures (`tests/fixtures/routing/`).

## 5. Out of scope here

- Classifier prompt content and batch-size values (V1-M2 implementation
  detail).
- Retrieval ranking/scoring (V1-M3).
- Episodic/profiling context in generation — deferred to V2-M5; v1 generator
  inputs remain email context + route decision + optional RAG context + system
  defaults only.
