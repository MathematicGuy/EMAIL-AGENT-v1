# RAG Status Reconciliation Design

## Goal

Make the two Email RAG status reports describe the code currently executed by
this checkout, while preserving the boundary between runtime guarantees and
historical evaluation evidence.

## Runtime model

With the Gemini provider, `build_semantic_memory()` loads the committed
Markdown corpus and selects Qdrant when `QDRANT_ENABLED=true`. It creates or
reindexes the configured collection when needed. If Qdrant is disabled or its
setup fails, the process attempts the deprecated in-repo hybrid retriever
(dense Gemini embeddings, BM25/RRF, optional Jina reranking, query expansion,
and MMR). If that fallback cannot be built, it uses `NullSemanticMemory`,
which returns a structured `no_results` response.

The FastAPI knowledge endpoint returns the port response unchanged. The React
frontend renders `no_results` as a no-match message; a transport failure
remains a distinct UI error.

## Evidence model

Qdrant has adapter, bootstrap, unit, and in-memory integration coverage. The
existing dense/BM25/RRF/Jina benchmark is a historical in-repo baseline, not
evidence that Qdrant Cloud has equivalent retrieval quality. No live-Qdrant
quality benchmark, calibrated abstention policy, semantic claim-grounding
evaluation, binary ingestion pipeline, or document-level authorization exists.

## Scope

Only `EMAIL-RAG-STATUS.md` and `RAG-EVALUATION-STATUS.md` are rewritten.
Existing user changes in `frontend/` are out of scope.
