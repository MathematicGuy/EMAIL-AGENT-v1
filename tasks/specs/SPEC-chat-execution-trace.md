# Chat Execution Trace — Minimal Spec

## Problem Statement

Users can see a coarse activity card and a selected-model label, but cannot see
the actual provider/model execution, provider-returned reasoning, or the names
of files retrieved for an answer. The label is not server-confirmed and the
current chat stream has no execution-trace contract.

## Solution

Keep the compact activity card. Clicking it opens a read-only right-side drawer
for that assistant message. The drawer shows the actual provider/model, chosen
effort, provider-returned reasoning when supplied, and retrieved filenames only.
The drawer receives the retained trace with its owning message and loses it
when its message or chat is deleted.

V1 supports MiMo and Mistral only. Gemini is deferred.

## User Stories

1. As a chat user, I can choose Fast or Reasoning before sending a MiMo or
   Mistral message, so that I control the response trade-off.
2. As a chat user, I can click an assistant activity card and inspect the
   actual execution details for that message without interrupting the chat.
3. As a chat user, I can revisit a previous message and see the same retained
   trace and retrieved filenames, until that message or chat is deleted.

## Implementation Decisions

- The existing assistant activity card stays a minimal process tracker. It is
  the only control that opens the trace drawer; raw reasoning is not rendered
  inline in the message flow.
- The approved drawer presentation is the Inspector layout: compact execution
  metadata, a readable reasoning section, and filename-only retrieved files.
  On wide screens, opening it contracts chat into a two-column layout and
  closing it expands chat to the full available width without a blank panel.
  On narrow screens, the layout stacks.
- Add one explicit, turn-scoped execution-trace contract. It carries
  server-confirmed provider, model, requested effort, provider-returned
  reasoning, retrieved filename strings, and a truncation state.
- Preserve the current aggregate activity snapshot. Do not overload it with
  repeated trace details or reasoning content.
- Current provider transports return the trace with the final reply; live
  token-by-token reasoning streaming is deferred.
- Persist the trace alongside the assistant turn and delete it atomically with
  the message/chat. There is no separate trace archive.
- V1 persistence is SQLite only: `ChatTurn` is already serialized as a JSON
  payload, so the optional trace is backward-compatible and needs no schema
  migration. PostgreSQL storage is deferred.
- Normalize provider outputs, not assumptions: display any reasoning returned
  by MiMo or Mistral in either Fast or Reasoning mode. If a provider returns no
  reasoning, the drawer states that no reasoning was returned for that message.
- Provide exactly two provider-specific modes:
  - MiMo: Fast requests thinking disabled; Reasoning requests thinking enabled.
- Mistral: Fast requests `reasoning_effort=none`; Reasoning requests
    `reasoning_effort=high`.
- A Reasoning option is available only when the resolved Mistral model supports
  it. The currently configured model must be validated or updated before the
  UI claims that Mistral Reasoning is available.
- The provider adapters must send the selected mode, parse MiMo
  `reasoning_content` and Mistral thinking blocks, and retain enough output
  capacity for reasoning. The current adapters only parse final content.
- Show retrieval filenames only. Do not add retrieved chunks, prompts, API
  keys, hidden model state, token/cost telemetry, or a full event timeline.
- Cap persisted reasoning and show an explicit truncation notice in the drawer.
- The drawer is read-only and must use the same session/project authorization
  as the owning assistant message.
- The benchmark/evaluator is not acceptance evidence until it returns a
  non-zero result for provider/auth failures. The current live run returned
  HTTP 401 for every request and produced no model reasoning.

## Testing Decisions

- Use the existing chat message stream plus turn-scoped history seam as the
  highest integration seam: prove a trace streams when supplied, persists,
  loads on card click, and is removed with its message.
- Unit-test provider normalization with representative MiMo and Mistral
  responses for both modes, including an absent reasoning field.
- Add a frontend behavior test: an activity card opens the drawer, the drawer
  renders confirmed details/filenames and truncation state, and it is read-only.
- Keep external providers mocked in automated tests. A separate opt-in live
  check may validate credentials and real returned fields, but must load the
  repository environment correctly, classify authentication failure, return a
  non-zero result, and never write a successful-looking report on failure.

## Out of Scope

- Gemini reasoning, universal effort levels, token/cost analytics, prompt or
  retrieved-content display, and an additional timeline/debug console.
- Changing model quality, routing/fallback policy, or exposing a chat tool to
  users.

## Further Notes

- Verified baseline: the local backend is healthy and the offline LLM
  integration suite passed (106 tests).
- The frontend dev command currently requests a dependency purge; it was not
  run to avoid altering the existing dependency tree.
