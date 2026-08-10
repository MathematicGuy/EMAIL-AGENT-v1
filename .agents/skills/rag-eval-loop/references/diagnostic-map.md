# Diagnostic Map

Maps each evaluation metric deficit to its **failure layer**, root-cause
patterns, and the JSONL fields to inspect.

---

## Retrieval Layer

### `coordinate_recall` < 0.85

**Failure layer**: Retrieval — the hybrid search did not return chunks covering
all required legal coordinates (`doc_id`, `chapter`, `article`).

**Root-cause patterns**:
1. **Dense score threshold too aggressive**: `min_dense_score` filters out
   relevant chunks whose embedding similarity falls below the cutoff.
2. **Chunk boundary misalignment**: The chunking strategy split a legal article
   across two chunks; only one half carries the coordinate metadata.
3. **Missing or stale index**: The Qdrant collection was not re-ingested after
   a document or chunking change.

**Inspection**: In `retrieval.jsonl`, compare each failing case's
`golden_contexts[].coordinates` against `retrieval.hits[].chunk.coordinates`.
Note which required coordinates are absent from hits.

---

### `evidence_recall` < 0.85

**Failure layer**: Retrieval — the retrieved chunk text does not contain the
required golden context substrings.

**Root-cause patterns**:
1. **All `coordinate_recall` root causes** (coordinates present but text
   truncated, or coordinates missing entirely).
2. **Embedding model weakness**: The dense encoder under-represents certain
   phrasings (e.g., legal numbering like "Điều 14, Khoản 3").
3. **Lexical retrieval disabled or misconfigured**: BM25 fallback would have
   caught exact substring matches that the dense model missed.

**Inspection**: In `retrieval.jsonl`, for each failing case, check whether the
golden context substring appears in any `retrieval.hits[].chunk.text`. If the
coordinate is present but text is absent, the chunk was split or truncated.

---

## Generation Layer

### `citation_validity` < 1.0

**Failure layer**: System Safety — the LLM generated citation tags `[C<n>]`
that reference chunk indices not present in the retrieved context.

**Root-cause patterns**:
1. **Prompt template error**: The system instruction does not clearly state
   that citations must reference only the provided `[C1]`..`[CN]` chunks.
2. **LLM hallucination under long context**: With many retrieved chunks, the
   model invents citation indices beyond the actual hit count.

**Inspection**: In `generation.jsonl`, for each failing case, compare
`generation.citations[].citation_id` against the chunk indices actually
provided in the prompt context.

---

### `citation_coverage` < 0.90

**Failure layer**: Prompting — the LLM failed to attach `[C<n>]` brackets to
non-abstained response sentences.

**Root-cause patterns**:
1. **Weak citation instruction**: The system prompt says "cite sources" but
   does not mandate per-sentence inline brackets.
2. **List/table formatting**: The LLM omits citations on bulleted or numbered
   list items.

**Inspection**: In `generation.jsonl`, read `generation.answer` and count
sentences missing `[C<n>]` markers.

---

### `abstention_accuracy` < 1.0

**Failure layer**: Guardrail / Hallucination — the LLM attempted to answer an
`unanswerable` golden case instead of returning the exact abstention phrase.

**Root-cause patterns**:
1. **Retrieval false positive**: The retrieval layer returned seemingly
   relevant chunks for an out-of-scope question, and the LLM used them.
2. **Abstention phrase mismatch**: The system prompt defines one abstention
   phrase but the evaluation checks for a different exact string.
3. **Model compliance drift**: A model update reduced instruction-following on
   refusal directives.

**Inspection**: In `generation.jsonl`, filter cases where `type ==
"unanswerable"` and `abstention_accuracy < 1.0`. Read `generation.answer` —
it should contain only the exact abstention phrase.

---

## Latency Layer

### `end_to_end_latency_ms_p95` > 3000 ms

**Failure layer**: Performance — the 95th-percentile response time exceeds the
SLA target.

**Root-cause patterns**:
1. **Retrieval bottleneck**: High `retrieval_latency_ms` on a small number of
   cases (check p95 vs. median spread in `retrieval.jsonl`).
2. **Generation bottleneck**: High `generation_latency_ms` caused by large
   context windows or slow LLM provider response times.
3. **Reranker overhead**: Cross-encoder reranking adding latency; check
   whether `reranker_model` is set and its impact.

**Inspection**: In `report.json`, compare
`aggregates.retrieval.overall.retrieval_latency_ms_p95` vs.
`aggregates.generation.overall.generation_latency_ms` to isolate which phase
dominates.
