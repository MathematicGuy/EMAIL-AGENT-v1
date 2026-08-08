# Hybrid Search Implementation Report

- **Date:** 2026-08-09
- **Implementation commit:** `ccf6539165bf9a0c87e7259db6bc5fd7242ba988`
- **Scope:** Local V1-M3 hybrid retrieval

## Outcome

The commit replaces dense-only local semantic retrieval with
`HybridSemanticMemory`. It preserves the existing retrieval contract while
adding lexical recall, deterministic fusion, and optional external reranking.

## Delivered scope

- [BM25 lexical search](../../../src/cowork_agent/integrations/rag/bm25.py)
  runs in memory with `k1=1.5` and `b=0.75`.
- [Reciprocal Rank Fusion](../../../src/cowork_agent/integrations/rag/rrf.py)
  combines dense and BM25 rankings with fixed `k=60` and deterministic
  chunk-ID tie-breaking.
- [Jina reranking](../../../src/cowork_agent/integrations/rag/jina_reranker.py)
  uses `jina-reranker-v2-base-multilingual`. A missing API key, transport
  error, or invalid response returns the unchanged RRF candidate order.
- [HybridSemanticMemory](../../../src/cowork_agent/integrations/rag/hybrid.py)
  applies the tenant gate, retrieves dense and lexical candidates, fuses them,
  optionally reranks them, and truncates the result to final top-k.
- Both [API composition](../../../src/cowork_agent/app.py) and
  [worker composition](../../../src/cowork_agent/orchestration/worker.py)
  now build the hybrid adapter and read the optional `JINA_API_KEY`.

## Privacy and access-control invariants

- Tenant ACL filtering occurs before query embedding and before BM25 corpus
  statistics or scoring, preventing cross-tenant ranking leakage.
- Raw email bodies and attachment content remain transient: this change does
  not persist or log them.
- The Jina boundary logs neither request content nor credentials and requests
  scores/indexes without echoed document text.
- Gmail remains read-only, and attachment processing remains out of scope.

## Tracked documentation and configuration updated

The implementation commit updated:

- [README](../../../README.md) and [agent guidance](../../../AGENTS.md)
- [environment example](../../../.env.example)
- [master comparison](../../master-comparison.md)
- [current architecture review](../../architectures/current-architectures/current-architecture-review.md)
- [current RAG architecture](../../architectures/current-architectures/current-rag-architecture.md)
- [email RAG architecture reference](../../references/EMAIL-RAG-ARCHITECHTURE.md)

These updates identify hybrid retrieval as implemented local behavior while
keeping Qdrant and the four-type memory system in target-state scope.

## Verification evidence

- 57 focused RAG/workflow tests passed.
- Ruff passed.
- Mypy passed across 57 source files.
- This evidence covers the implemented retrieval path; it is not a claim that
  the repository's full test suite passed.

## Evidence boundary and next evaluation

The passing tests and static checks are **implementation proof**: they verify
BM25, RRF, fallback behavior, orchestration wiring, and relevant contracts.
They are not **retrieval-quality benchmark proof**.

Dense and BM25 indexes remain in memory; production Qdrant is not implemented.
Jina is optional and requires an external service when enabled. A comparative
benchmark over a controlled golden set—reporting at least Hit@K and MRR for
dense-only, hybrid, and hybrid-plus-reranker variants—is the next evaluation
step. No comparative retrieval-quality gain is claimed by this report.
