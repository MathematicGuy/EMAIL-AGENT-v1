# Enterprise RAG & Vector Memory Subsystem (Level 1 Architecture)

**Architecture level:** Level 1 — Deep-Dive RAG & Vector Memory Subsystem  
**Status:** Live / Implemented  
**Primary Owner:** `src/cowork_agent/integrations/rag`  
**Target Alignment:** Fully Aligned with [TARGET-ARCHITECTURE.md §1, §2 & §3](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md), [ADR-004](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-004-chat-native-task-episodes.md), and [ADR-007](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-007-project-scoped-classifier-gated-user-documents.md)

---

## 1. Subsystem Purpose & Capabilities Matrix

The Enterprise RAG & Vector Memory Subsystem provides high-precision, low-latency semantic retrieval over committed enterprise knowledge documents (`data/extracted/*.md`) and classifier-gated user project documents.

| RAG Capability | Live Implementation | Authoritative Module Location |
|---|---|---|
| **Corpus Ingestion** | Offline CLI for Markdown, DOCX, and PDF extraction with SHA-256 hash manifest verification. | [ingestion_cli.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/ingestion_cli.py) & [knowledge_base.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py) |
| **Parsing & Chunking** | Structure-aware hierarchical chunking (ATX H1-H6 plus plain-text `Phần`/`Chương`/`Mục`/`Điều`/`Bước`); every chunk repeats its heading breadcrumb in its own text; target 1200 / max 2000 chars; page-aware for user documents. | [markdown_chunking.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/markdown_chunking.py) |
| **Embedding Adapters** | Dual embedding support for Jina AI Embeddings (`v5`) and Gemini Embeddings API. | [embeddings.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/embeddings.py) |
| **Primary Vector Store** | In-process 4-bit TurboQuant index (`.data/turbovec_index.tvim`) wrapped with BM25 + RRF. | [turbovec_memory.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py) |
| **Quantized Memory Store** | In-process 4-bit TurboQuant index (`.data/turbovec_index.tvim`) for low-footprint local vector search. | [turbovec_memory.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py) |
| **Hybrid & Lexical Search** | Dense matrix cosine search + Okapi BM25 lexical search adapter fused via Reciprocal Rank Fusion (`k=60`). | [hybrid.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/hybrid.py) & [bm25.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/bm25.py) |
| **Cross-Encoder Reranking** | Jina Cross-Encoder Reranker (`jina-reranker-v2-base-multilingual`) for precision candidate reranking. | [jina_reranker.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/jina_reranker.py) |
| **Diversity & Query HyDE** | Query guard, domain prefix expansion, HyDE hypothetical document generation, and MMR diversification (`lambda=0.7`). | [query_transform.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/query_transform.py) & [mmr.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/mmr.py) |
| **User Project Docs RAG** | Dedicated vector store for uploaded user files with workspace/user/project isolation and classifier gating. | [project_documents.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/project_documents.py) |

---

## 2. Corpus Ingestion Interface & Indexing Boundary

The document ingestion pipeline (DOCX/PDF conversion, SHA-256 manifest tracking, atomic Markdown generation) operates as a standalone subsystem. For the full ingestion pipeline architecture, see **[06-knowledge-and-document-ingestion-pipeline.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/06-knowledge-and-document-ingestion-pipeline.md)**.

```mermaid
flowchart LR
    INGEST["Ingestion Pipeline Subsystem<br/>(06-knowledge-and-document-ingestion-pipeline.md)"] --> MD["Committed Markdown Corpus<br/>(data/extracted/*.md)"]
    MD --> LOADER["Corpus Loader & Chunker<br/>(load_corpus / normalize + hierarchical<br/>target 1200, max 2000)"]
    LOADER --> EMBED["Embedding Adapter<br/>(Jina v5 / Gemini Embeddings)"]
    
    EMBED --> STORE_TURBO["Turbovec 4-Bit Index<br/>(.data/turbovec_index.tvim)"]
```

1. **Corpus Interface (`knowledge_base.py`):** `load_corpus()` deterministically reads the committed Markdown documents from `data/extracted/*.md`, runs `normalize_structure()` to promote plain-text structural headings (`Điều 1. …`) to ATX, then chunks along the resulting heading hierarchy. Each `KnowledgeChunk` opens with its heading breadcrumb, so a retrieved fragment names the article it came from for both dense and BM25 search; chunks target 1200 and never exceed 2000 characters.
2. **Turbovec Quantized Indexing (`turbovec_memory.py`):** Pads embedding dimensions to multiples of 8 and builds a 4-bit TurboQuant quantized index saved to `.data/turbovec_index.tvim`.


---

## 3. Multi-Backend Retrieval Architecture

```mermaid
flowchart TB
    REQ["SemanticRetrievalRequest<br/>(query, knowledge_gaps, filters)"] --> GUARD{"Query Guard<br/>(query_guard.py)"}
    
    GUARD -->|Invalid / Short Greeting| NULL_RES["Empty Retrieval Response"]
    GUARD -->|Valid Query| STATUS_CHECK["Document Status & ACL Validation<br/>(document_status == 'ready')"]
    
    STATUS_CHECK --> TRANSFORM["Query Transformer<br/>(Domain Prefixes & HyDE Expansion)"]
    TRANSFORM --> LADDER{"Provider Selection<br/>(RAG_STORE_PROVIDER)"}

    LADDER -->|turbovec| TURBO["Turbovec 4-Bit Dense<br/>(.data/turbovec_index.tvim)"]
    LADDER -->|unknown / qdrant / failed / disabled| NULL_MEM["NullSemanticMemory<br/>(structured no_results)"]

    TURBO --> HYBRID["HybridSemanticMemory<br/>(dense + BM25 + RRF)"]
    HYBRID --> RESP["SemanticRetrievalResponse<br/>(chunks, citations, scores)"]
    NULL_MEM --> RESP
```

### Retrieval Execution Ladder:

1. **Status filter:** in-process retrievers honor `document_status` on the request before scoring.
2. **Query Transformation (`query_transform.py`):** Applies domain prefixes ("Quy trình thủ tục...", "Hướng dẫn quy định...") and generates hypothetical passages via HyDE.
3. **Hybrid wrapper (`hybrid.py`):**
   - Accepts an injected dense `SemanticMemoryPort` (Turbovec) from `build_semantic_memory()`.
   - Executes lexical keyword search via `BM25SearchAdapter` over the same company corpus.
   - Fuses dense and lexical ranks with Reciprocal Rank Fusion (`ReciprocalRankFusion`, `k=60`).
   - Optionally reranks with Jina and diversifies with MMR (eval / advanced path; factory wrap is dense + BM25 + RRF).
4. **Graceful Degrader (`null_memory.py`):** Returns `RetrievalStatus.NO_RESULTS` if stores or upstream embedding APIs are unavailable, ensuring digest runs and chat turns never crash.

---

## 4. User Project Documents Subsystem RAG Engine

In addition to enterprise company knowledge, the project provides a dedicated vector store for user-uploaded project documents ([ADR-007](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-007-project-scoped-classifier-gated-user-documents.md)):

| Aspect | Technical Implementation |
|---|---|
| **Authoritative File** | [project_documents.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/project_documents.py) |
| **Storage & Extraction** | Private Supabase Storage bucket + page-aware chunking (`page_start`, `page_end`) for PDF/DOCX documents. |
| **Vector Isolation** | Per-project Turbovec `.tvim` plus a six-condition Postgres allowlist (`workspace_id`, `user_id`, `project_id`, selected ids, ready, unexpired). |
| **Classifier Gating** | Access is gated behind `USER_DOCUMENTS_ENABLED=true` and evaluated by `ChatRoutingService` intent classification prior to search execution. |

---

## 5. Caller Integrations

### 5.1 Email Action Plan Subsystem Integration

When an incoming email is classified as `RETRIEVE_RAG` by `routing.py`, `ActionPlanWorkflow` invokes `SemanticMemoryPort.retrieve()`. Retrieved chunks carrying provenance citations (`document_id`, `section`, `relevance_score`) are injected into the Gemini/Groq LLM prompt to ground the generated Action Plan.

### 5.2 AI Chat Subsystem Integration

`SemanticChatMemoryAdapter` wraps `SemanticMemoryPort` for the 4-Type Memory Gateway. During chat turns, `read_semantic_context()` retrieves background facts and injects them as verified `current_company_evidence` into the system instructions for the LLM chat reply generator.

---

## 6. Vector Stores & Configuration Summary

| Store Engine | Provider Value | Index Location / Address | Primary Use Case |
|---|---|---|---|
| **Turbovec 4-bit** | `turbovec` (default) | `.data/turbovec_index.tvim` | Company RAG for Email + Chat Type 4 (Hybrid dense + BM25 + RRF). |
| **Project hybrid** | n/a (ADR-008) | Postgres `project_document_chunks` + `var/project-indexes/{id}.tvim` | User-uploaded project documents. |
| **Null Memory** | `none` / retired `qdrant` | N/A | Degraded state fallback preventing application crashes. |
