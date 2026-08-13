# Chat RAG evidence panel

## Goal

Let a user inspect the exact retrieval evidence used for each AI answer inside
the same chat session. The UI shows a score and a short text preview for every
retrieved chunk, with a dialog for the full chunk text. Evidence remains
available when a saved session is reopened; the UI must not repeat retrieval
to reconstruct history.

## Scope

This applies to both company-corpus RAG and project-document RAG. It does not
change retrieval routing, ranking, answer generation, or the document panel.
Raw Gmail content remains excluded.

## Data contract

Add a bounded `rag_evidence` collection to the assistant turn and to the SSE
completion event. One evidence item contains:

- immutable chunk ID, document ID, title, optional section and source URL;
- retrieval source (`company_knowledge` or `project_document`);
- `relevance_score` and optional `rerank_score`;
- retrieval status for the turn;
- a server-derived preview (first 400 normalized characters); and
- the full retrieved chunk text.

The server serializes the same evidence on the durable `ChatTurn`. The session
messages endpoint returns that stored payload. The existing coordinate-only
citation payload remains compatible for project-document citations.

Bound evidence to the retrieved top-k (maximum five chunks) and validate text
size before persistence. Never attach evidence to a response if retrieval was
not requested; represent a requested failed/no-result retrieval with status
and an empty evidence list.

## Backend flow

1. The semantic-memory adapter returns chunks, scores and retrieval status.
2. The chat controller captures the exact serialized evidence before it calls
   the reply provider.
3. On completion it emits the evidence through SSE and persists it with the
   assistant turn.
4. A reopened session returns the stored turn evidence without touching
   Qdrant, embedding services or the LLM.

## UI

Each completed assistant message with a retrieval outcome renders a collapsed
`RAG evidence` disclosure below its response. Its label shows the retrieval
status and chunk count. Expanding it renders ranked cards with document title,
section, score, a 400-character preview and a keyboard-accessible `View full
chunk` button. The button opens a modal dialog containing the full chunk text
and document metadata; Escape and the close button dismiss it.

For `no_results`, `timeout` or unavailable retrieval, the disclosure shows the
status and explains that no chunks were supplied. It must not imply that the
question is unsupported when the retrieval service failed.

## Testing

- Backend contract tests prove an RAG turn emits and persists bounded evidence
  with score and content, while a non-RAG turn has no evidence.
- Session-history tests prove rehydration returns the same evidence without a
  retrieval call.
- Frontend hook tests prove SSE evidence attaches to the correct assistant
  message and history parsing restores it.
- Component tests cover collapsed cards, preview/score rendering, full-text
  dialog and empty/error states.
- Run frontend tests, type checking and backend focused tests; manually verify
  a completed chat in the browser at desktop and narrow viewport widths.
