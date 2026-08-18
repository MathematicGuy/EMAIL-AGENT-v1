# Parallel Chat Lifecycle Design

## Goal

Submitting a prompt creates a durable chat immediately. Navigating to another
chat never cancels work in progress, and distinct chats may generate in
parallel. A single chat still permits only one active assistant response.

## Confirmed behaviour

- Create the session and pending user turn before model generation.
- Add the chat to the Project sidebar immediately with a prompt-derived title
  and a visible `Generating` state.
- `New chat` changes the visible draft; it does not abort another chat.
- Each chat owns its transcript, draft, generation status, and AbortController.
- Drafts survive navigation only for the lifetime of the current page.
- Returning to a generating chat restores its optimistic prompt and streamed
  response immediately.
- Completion replaces the temporary title with the generated title. Inactive
  chats receive an unread marker without taking focus.
- Failure, cancellation, rate limiting, and reload interruption retain the
  submitted prompt and expose retry on the same logical turn.
- There is no client-wide concurrency limit. Backend usage limits remain the
  authority.

## Persistence contract

`chat_turns` becomes a lifecycle record rather than completed-history only.
The migration makes `assistant_message` nullable and adds:

- `status`: `generating`, `completed`, `failed`, `cancelled`,
  `usage_limited`, or `rate_limited`.
- `idempotency_key`: stable for a logical user submission and unique within a
  session.
- `error_code`: optional machine-readable terminal failure.

Existing rows backfill to `completed`. The history repository exposes
idempotent begin/complete/fail operations. A row still marked `generating`
when fetched without a live browser runtime is presented as `interrupted` by
the frontend; no extra database state is required for a lost connection.

## Streaming and cancellation

The message request persists the pending turn before routing or provider work.
The first SSE lifecycle event identifies the durable turn. Completion updates
the same row. Provider errors update it to a recoverable terminal state.

Browser navigation keeps the fetch alive. The Stop action aborts only the
selected chat and calls a cancellation endpoint for its durable turn. A page
reload may close the stream without a cancellation call, leaving `generating`
as evidence of an interrupted attempt.

## Frontend state

The hook keeps a page-session map keyed by a local new-chat key or server
session ID. Each entry owns messages, draft, selected attachments, generation
status, unread state, and controller. The existing hook remains the public
adapter used by `Dashboard`; active-chat values are projections of the map.

The sidebar consumes per-chat status rather than the active chat's single
`isGenerating` flag. Delete remains disabled for any chat currently generating.

## Retry semantics

Retry reuses the pending turn's idempotency key and replaces its assistant
placeholder. It never appends a second user prompt. A distinct submission gets
a new key and starts immediately, even when other chats are generating.

## Verification

- Backend unit tests prove pending-before-provider, completion update, terminal
  failures, cancellation, and idempotent retry.
- Persistence integration tests prove migration/backfill and lifecycle upsert.
- Frontend hook tests reproduce rapid submit -> New -> submit, independent
  streaming, draft restoration, return-to-stream, stop isolation, and retry.
- Component tests cover generating labels and unread markers.
- Browser verification exercises the original race with two deliberately slow
  chats and checks the console/network for errors.
