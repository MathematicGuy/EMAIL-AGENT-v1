# Tuning Knobs

Every parameter and code path that influences evaluation metrics. Grouped by
the failure layer they affect. The agent consults this when forming a
**hypothesis** in Step 4.

---

## Retrieval Knobs

These settings live in [`settings.py`](file:///e:/VIN-INTERNSHIP/Cowork-RAG/src/settings.py)
and are overridable via environment variables or `rag-eval` CLI strategy flags.

| Knob | Setting / CLI Flag | Default | Affects | Typical Adjustment |
|------|-------------------|---------|---------|-------------------|
| Dense score floor | `min_dense_score` | `0.35` | `coordinate_recall`, `evidence_recall` | Lower (e.g. `0.25`) to include borderline-relevant chunks |
| Retrieval limit | `retrieval_limit` | `5` | `coordinate_recall`, `evidence_recall` | Increase (e.g. `8`) for multi-hop cases needing more chunks |
| Reranker model | `reranker_model` / `--reranker-model` | `""` (disabled) | `coordinate_recall`, `evidence_recall`, latency | Enable cross-encoder reranking to promote relevant chunks |
| Rerank candidate pool | `rerank_candidate_limit` | `50` | Reranker input diversity | Increase if reranker misses relevant chunks in initial pool |
| Lexical candidate pool | `lexical_candidate_limit` | `500` | BM25 retrieval breadth | Increase for documents with rare legal terms |
| MMR diversification | `enable_mmr` / `--enable-mmr` | `false` | Evidence spread across distinct articles | Enable to reduce redundant chunks from same section |
| MMR lambda | `mmr_lambda` / `--mmr-lambda` | `0.7` | Balance relevance vs. diversity | Lower (e.g. `0.5`) for more diversity |
| Query transform | `query_transform_mode` / `--query-transform-mode` | `"none"` | Multi-hop recall | `"hyde"` generates hypothetical doc; `"multi_query"` expands query |
| Multi-query count | `multi_query_n` | `3` | Query expansion breadth | Increase for broader coverage |
| MRL search | `enable_mrl` / `--enable-mrl` | `false` | Fast approximate search | Enable for latency improvement |
| MRL fast dimension | `mrl_fast_dim` | `128` | MRL precision vs. speed | Lower for speed, higher for accuracy |
| Enrichment | `enable_enrichment` / `--enable-enrichment` | `false` | Index quality via LLM-generated summaries | Enable to improve semantic matching |

---

## Generation Knobs

These affect LLM output quality, citation behavior, and abstention.

| Knob | Location | Affects | Typical Adjustment |
|------|----------|---------|-------------------|
| System prompt | [`src/prompts/answer_v2.yaml`](file:///e:/VIN-INTERNSHIP/Cowork-RAG/src/prompts/answer_v2.yaml) | `citation_coverage`, `citation_validity`, `abstention_accuracy` | Strengthen citation-bracket and abstention instructions |
| Generation model | `gemini_model` / `openrouter_model` / `openai_model` | All generation metrics | Switch to higher-capability model |
| Provider timeout | `provider_timeout_seconds` | `generation_latency_ms` | Increase if model responses are truncated by timeout |
| Max retries | `provider_max_attempts` | Error rate | Increase for unreliable providers |
| Structured retries | `structured_max_retries` | Parse error rate | Increase if structured output parsing fails |

---

## Ingestion Knobs

These affect the chunk index quality and must be followed by re-ingestion.

| Knob | Location | Affects | Typical Adjustment |
|------|----------|---------|-------------------|
| Embedding model | `embedding_model` | `coordinate_recall`, `evidence_recall` | Switch to higher-quality encoder |
| Vector dimensions | `vector_size` | Embedding precision | Match to embedding model output dimensions |
| Qdrant collection | `qdrant_collection` | Index isolation | Use separate collection for A/B testing |
| Chunk strategy | [`src/ingestion/`](file:///e:/VIN-INTERNSHIP/Cowork-RAG/src/ingestion/) | Chunk boundary alignment | Modify chunk splitting to respect legal article boundaries |

> [!IMPORTANT]
> After changing any ingestion knob, re-index with:
> ```powershell
> rag-eval ingest --source data/raw/01_2021_ND-CP_283247.docx --force-reingest
> ```
> Then re-run `rag-eval e2e` to capture the new index state.

---

## CLI Strategy Overrides

The `rag-eval` CLI accepts `--reranker-model`, `--enable-mmr`, `--mmr-lambda`,
`--query-transform-mode`, `--enable-mrl`, and `--enable-enrichment` as
per-run overrides. These do **not** modify `settings.py` — they apply only to
the current evaluation run, allowing quick A/B experiments without touching
configuration files.

Example experiment testing HyDE query transformation:
```powershell
rag-eval e2e --query-transform-mode hyde --name "hyde-experiment"
```
