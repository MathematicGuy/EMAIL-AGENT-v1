# ADR-007 — Project-scoped, classifier-gated user documents

- Status: Accepted
- Date: 2026-08-12
- Supersedes: ADR-006 and the withdrawn always-retrieve Project design

## Context

Chat needs a durable backend-owned container so users can keep unrelated document sets and
histories isolated. Retrieval must still avoid unnecessary embedding/vector calls and must
handle deictic or ambiguous turns better than phrase matching.

## Decision

1. The ownership hierarchy is `tenant → user → project → documents + chat sessions`.
   PostgreSQL is authoritative for Projects and document metadata. Browser localStorage may
   remember only the active Project ID.
2. Every chat session is permanently bound to one Project. Missing `project_id` at session
   creation resolves or creates the user's deterministic `Default Project`; foreign IDs are
   returned as 404.
3. The structured intent classifier is the sole authority that may originate document
   retrieval. Deterministic code may only validate or narrow its decision. The tool axis is
   disabled for this release.
4. Project documents use a separate Qdrant collection and an ACL filter containing tenant,
   user, Project, ready status, expiry, and optional document IDs. Ownership and the filter
   are established before embedding I/O. Company evidence is never a fallback.
5. Source bytes live in a private Supabase Storage bucket and are uploaded through short-lived
   signed URLs. PostgreSQL owns durable ingestion and cleanup jobs; Redis may notify workers,
   while PostgreSQL polling remains the durable fallback. Extracted text is retained only in
   the Project Qdrant collection.
6. Profile and eligible episodic retrieval remain user-wide. Sessions, short-term history,
   and document retrieval are Project-scoped. TaskEpisodes record `project_id` only for
   provenance and never store document text.
7. Document citations are server-validated coordinates. The model may return only citation
   IDs from evidence supplied for the current turn; the API maps them to Project/document,
   title, section, and page range.
8. `USER_DOCUMENTS_ENABLED=true` and `CHAT_INTENT_CLASSIFIER_ENABLED=true` are the
   release defaults; operators may explicitly disable either feature as a kill switch.
9. Project documents reuse the Email/Knowledge Markdown chunker: H1/H2 sections, paragraph
   packing, 1,200-character soft cap, no overlap, deterministic IDs, and page coordinates.
   They use Gemini `gemini-embedding-2` with 3,072-dimensional retrieval document/query
   vectors. Company/Email RAG embedding configuration remains unchanged.

## Consequences

- Deleting a Project first makes its documents and sessions retrieval-ineligible, then durable
  cleanup jobs repeatedly remove Qdrant points and Supabase objects. Deleting the default
  Project creates an empty UUID replacement.
- No Project sharing, Gmail attachment ingestion, corpus promotion, or executable chat tools
  are part of this decision.
- The standalone Email Agent and its company RAG lifecycle are unchanged.

## Links

- [PRD-v3](../prds/PRD-v3-chat-with-user-documents.md)
- [Technical SPEC](../specs/SPEC-chat-with-user-documents.md)
- [Target Architecture §21](../../docs/architectures/TARGET-ARCHITECTURE.md)
- [ADR-008](ADR-008-turbovec-project-document-plane.md) — accepted; supersedes clauses 4 and 5
