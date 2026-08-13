# Chat-with-documents resilience

The Project-document plane is optional to core API availability even though its
feature flag defaults to enabled. PostgreSQL, Supabase Storage, Gemini embeddings,
Qdrant initialization, or the document worker may degrade that plane without
preventing `/health`, ordinary chat, or Email Agent startup.

`GET /v1/cowork/chat/document-health` reports `ready` only when all document
dependencies are usable and the worker heartbeat is no older than two minutes.
The React surface starts fail-closed, mounts document controls only for a `200`
`ready` response, and rechecks health every ten seconds.

Retrieval has one end-to-end deadline covering both PostgreSQL authorization
reads, embedding, both Qdrant attempts, and the final PostgreSQL readiness check.
Timeout and dependency failure return announced degraded evidence rather than
unsourced generation.

Transient ingestion dependency failures are durably requeued up to three total
attempts with a 30-second delay. Exhaustion records a safe dependency-specific
failure code. Upload cancellation propagates across hashing boundaries and every
network request in register, signed PUT, completion, and polling.

OCR is explicitly out of scope for this increment. PDFs requiring OCR fail closed
as `ocr_unavailable`; partial native text is never indexed.
