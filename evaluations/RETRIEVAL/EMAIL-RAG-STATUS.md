# Email RAG — Architecture Implementation Status
> **Document status:** current snapshot as of 2026-08-14. This report describes
> the code in this checkout, not a target architecture.

## Executive summary

Email RAG is implemented as a retrieval-only capability behind
`SemanticMemoryPort`. `build_semantic_memory()` loads the committed Markdown
corpus and builds Turbovec 4-bit hybrid memory (dense + BM25 + RRF). Unknown,
retired (`qdrant`), or failed providers degrade to `NullSemanticMemory`, which
returns a structured empty response rather than blocking an email run.

```mermaid
flowchart LR
    A[LLM provider] --> B[Load data/extracted Markdown corpus]
    B --> C{RAG_STORE_PROVIDER}
    C -->|turbovec| D[Turbovec + BM25 + RRF]
    C -->|none / qdrant / other| F[NullSemanticMemory]
    D -->|setup fails| F
    D --> G[SemanticMemoryPort]
    F --> G
    G --> H[Email workflow and /knowledge/chat]
```

## Implemented architecture

| Area | Status | Current behavior and evidence |
|---|---|---|
| Corpus source | Implemented | `knowledge_base.py` loads committed `data/extracted/*.md`, creates document/section metadata, and does not ingest raw email bodies. |
| Turbovec store | Implemented | `TurbovecSemanticMemory` 4-bit `IdMapIndex` over `.data/turbovec_index.tvim`, wrapped with BM25 + RRF. |
| Empty-result fallback | Implemented | When no memory can be built, `NullSemanticMemory` returns `retrieval_status=no_results` with no chunks. |
| Email workflow wiring | Implemented | `DigestWorker` invokes retrieval for `RETRIEVE_RAG`, skips it for `DIRECT_PLAN`, forwards chunks to the generator, and records missing information when retrieval is empty or unavailable. |
| Citation boundary | Implemented | Validation removes generated citation IDs that are not present in the retrieval response for the current request. |
| Knowledge HTTP API | Implemented | `app.py` exposes readiness, document-list, and `/v1/mail-todo/knowledge/chat` endpoints. The chat endpoint returns the port's chunks, status, and latency; it does not generate an answer. |
| React presentation | Implemented | The React client renders `retrieval_status=no_results` as a no-match state. A client transport timeout/error remains distinct from an API no-result response. |

## Runtime behavior and degradation

1. Startup calls `build_semantic_memory()`.
2. Default `RAG_STORE_PROVIDER=turbovec` builds the hybrid store over the
   committed corpus.
3. A Turbovec bootstrap failure, or `none` / `qdrant` / unknown provider,
   produces `NullSemanticMemory`. Retrieval then succeeds as an API operation
   with `no_results`, rather than failing the digest workflow.
4. A healthy retriever returning no matching chunks also returns `no_results`.
   This is distinct from a React-client-to-backend network/timeout failure.

## Security and data boundaries

- Gmail access remains read-only.
- Raw email bodies and attachment content are transient; the RAG corpus comes
  only from repository Markdown files.
- Tenant scope is filtered in the in-process retrievers before scoring.
- Per-user, group, document-status, and document-level ACL policies are not
  implemented. The current corpus is effectively company-wide within its
  tenant scope.

## Known gaps and limits

| Gap | Status | Why it matters |
|---|---|---|
| Live embedding quality vs hashing | Partial | `scripts/evaluate_retrieval.py` measures dense / turbovec / hybrid stacks; there is no Qdrant control group. |
| Calibrated abstention | Missing | `no_results` is structurally supported, but no validated runtime score or margin policy separates unrelated Vietnamese queries from relevant corpus content. |
| Active end-to-end deadline | Partial | Embedding/reranker transports have their own timeouts, but `RetrievalLimits.timeout_ms` is not enforced as one deadline across every remote step. |
| Reranker observability | Missing | Jina safely falls back, but the runtime response does not report whether reranking was applied or bypassed. |
| Semantic grounding | Missing | Citation IDs are constrained to retrieved chunks; no evaluation proves generated plan claims are entailed by those chunks. |
| Binary ingestion | Partial | The administrator CLI converts local DOCX and native-text PDF files into Markdown. Scan, image-based, and mixed PDFs fail safely with `mistral_not_configured` until Mistral OCR is configured; XLSX/PPTX, upload, and Gmail-attachment ingestion do not exist. |
| Corpus administration | Missing | No persistent document registry, version history, incremental update, or asynchronous ingestion pipeline exists. |

## Operational checks

| Check | Expected result |
|---|---|
| `GET /v1/mail-todo/knowledge/ready` | `ready` when a non-null store and corpus are available; `degraded` for `NullSemanticMemory`; `unavailable` when the corpus cannot load. |
| `POST /v1/mail-todo/knowledge/chat` with no matching chunk | HTTP 200 with `retrieval_status: "no_results"` and an empty `chunks` list. |
| React query for the same response | A no-match state. |
| Turbovec unavailable at boot | Warning in backend log, then `NullSemanticMemory`. |

## Source of truth

- `src/cowork_agent/integrations/rag/bootstrap.py`
- `src/cowork_agent/integrations/rag/knowledge_base.py`
- `src/cowork_agent/integrations/rag/turbovec_memory.py`
- `src/cowork_agent/integrations/rag/hybrid.py`
- `src/cowork_agent/ingestion_cli.py`
- `src/cowork_agent/app.py`
- `src/cowork_agent/features/email_action_plan/workflow.py`
- `src/cowork_agent/gui/app.py`

## Local knowledge ingestion

The `mail-todo-ingest-knowledge` administrator CLI accepts `--source`,
`--output`, `--force`, and `--dry-run`. It deterministically discovers local
DOCX/PDF files, writes non-empty Markdown atomically, and records hashes in an
ingestion manifest. Run it with `KNOWLEDGE_INGEST_OCR_ENABLED=false` until
`MISTRAL_API_KEY` is configured.

Native-text PDFs and DOCX files are supported. PDFs that need OCR fail with
`mistral_not_configured` and do not create partial output. The CLI never
downloads Gmail attachments, has no upload API, and does not write the
Turbovec snapshot; after a successful ingestion, restart the API/worker so
`.data/turbovec_index.tvim` is rebuilt.
- [RETRIEVAL-EVALUATION-STATUS.md](./RETRIEVAL-EVALUATION-STATUS.md) — evaluation & test coverage map
- [TARGET-ARCHITECTURE.md](../../architectures/TARGET-ARCHITECTURE.md) — target design and milestone gap analysis
