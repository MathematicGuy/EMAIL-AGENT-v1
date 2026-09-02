# ADR-014 — The turn pipeline stays one function; only settlement is extracted

- Status: Accepted (candidate 04 complete: slices 04-1…04-3)
- Date: 2026-08-25
- Decision makers: Product/Engineering team
- Relates to: `ChatController.stream_message`; partners [ADR-013](ADR-013-composition-as-typed-value.md)

## Context

`ChatController.stream_message` drives one chat turn end to end and was ~625
lines. The candidate-04 roadmap proposed splitting it into six stages over a
single `TurnState` value: admit → route → assemble-context → generate →
extract-artifacts → settle. The roadmap itself flagged the split as borderline
and required an adversarial review before any code was written, because a bad
split produces six shallow modules passing a fat state around — complexity
relocated rather than concentrated.

That review was held. It rejected the six-stage split on evidence, and the
same evidence pointed at a different, narrower extraction.

## Decision

**`stream_message` stays one linear function.** No `TurnState`, no stage
objects, no driver loop.

Three narrower changes were made instead:

1. **`TurnJournal`** (slice 04-1, `features/ai_chat/turn_journal.py`) owns the
   evolving `ChatTurn` while it is generated. `record()` is the single
   phase-boundary call: transition the activity snapshot, persist it, refresh
   the live-turn registry, return the event to yield.
2. **`CancellationGuard`** (slice 04-1, same module) answers "must this turn
   stop?" once, replacing six sites that each re-spelled
   `turn_id in self._cancelled_turn_ids or await is_cancelled()`.
3. **`TaskEpisodeSettler`** (slice 04-3,
   `features/ai_chat/task_episode_settlement.py`) holds both halves of landing
   a task episode on a turn — the first attempt and the retry — which had been
   mirror images ~60 lines apart. `TurnAborted` collapses the two duplicated
   "durable write failed" blocks onto one `_persist_completed_turn`.

Slice 04-2 separately promoted the three memory-gateway reads the controller
needs (`read_active_turns`, `read_project_documents`, `read_task_episode`) to
the public interface, so scope enforcement is no longer reached through a
private name.

## Rationale

- **Deletion test — the stage split fails it.** 28 locals cross the proposed
  stage boundaries (`turn_id`, `guard`, `journal`, `emitted`,
  `routing_outcome`, `context_request`, `searches_information`,
  `final_activity`, `response_mode`, `project_documents`, `context`,
  `generation_context`, `assistant_message`, `task_proposal`,
  `generated_report`, `conversation_title`, `selected_citation_ids`, the three
  `trace_*`, `reasoning_parts`, `rag_evidence`, `retrieval_status`,
  `execution_trace`, `generated_artifact_refs`, `task_requested`, `turn`,
  `pending_task_episode`). A stage taking 28 fields and returning 28 fields
  has an interface as wide as its implementation: zero depth. `TurnState`
  would be the function's local scope with a class declaration around it.
- **It is not even a pipeline.** `response_mode` is decided in *route* and
  rewritten in *assemble-context* when project-document evidence is missing or
  degraded. A later stage reaches back and overrules an earlier one, so the
  stages could not be reasoned about independently anyway.
- **Termination is not stage-shaped.** 12 early `return`s end the turn, spread
  across all six proposed stages and several nested three or four levels deep
  — including one inside the `stream_reply` delta loop. Preserving them across
  stage boundaries requires every stage to be an async generator plus an
  explicit termination protocol, which is a large share of the complexity the
  split was meant to remove.
- **The replay buffer is not stage-shaped either.** `emitted` is appended at
  17 sites in every proposed stage, and `replay_prefix` is snapshotted
  *mid-settle* — inside the `MemorySourceUnavailableError` branch, before its
  warning is appended, then extended again after the final activity event. It
  has no stage boundary to live on.
- **What the extraction is actually worth.** The settlement pair was real
  duplication on the page: the same citation/proposal events built twice from
  the same helper against the same caches. That is a deep module — a narrow
  interface over a genuinely separable concern with its own failure modes
  (retryable outage vs rejected record). The five other "stages" are not.
- **Cost of the alternative had it shipped.** Six modules, a 28-field shared
  value, and a termination protocol, to make the same function longer to read
  than it was.

## Consequences

- `stream_message` splits only into the public traced entry (scope check,
  guard construction, `TurnAborted` handler) and `_stream_turn` (the body).
  `controller.py` went 1382 → 1207 lines across candidate 04.
- The turn body stays linear and is read top to bottom. Anyone proposing to
  stage-split it again should start from the five facts above; if the state
  crossing boundaries has meaningfully shrunk since, the answer may change.
- Two mutation-verified R2 cases were added for paths the suite did not cover:
  an explicit cancel arriving mid-delta-stream, and the task path's completion
  failure. The second needs a history double that fails only the terminal
  `completed_at` write — a blanket-failing one aborts at the earlier write
  before the task fork runs.
- `MemoryGateway`'s public surface grew by three reads; its allowlist test
  (`test_gateway_exposes_only_authorized_durable_write_operations`) records
  why each is authorized.
