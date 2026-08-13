# Ingestion Pipeline Brainstorming & Architecture Reference Board

**Status:** Reference & Study Guide  
**Location:** `docs/references/understand/ingestion-pipeline-brainstorming.md`  
**Related Architecture Docs:**  
- [06-knowledge-and-document-ingestion-pipeline.md](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/06-knowledge-and-document-ingestion-pipeline.md)
- [01-email-action-plan-and-rag.md](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/01-email-action-plan-and-rag.md)
- [02-ai-chat-and-typed-memory.md](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/02-ai-chat-and-typed-memory.md)
- [05-rag-architecture.md](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/05-rag-architecture.md)

---

## 1. Core Question & Conceptual Clarification

### Q: Is the ingestion pipeline a centralized document ingestion pipeline for Email RAG and Chat with PRD (including LLM + Intent classifier and RAG)? Do they both use the same ingestion pipeline?

### Key Architectural Answers

1. **Shared Company Knowledge Base (Centralized Ingestion):**
   * **Yes**, for company-wide administrator documentation (`.docx`, `.pdf`).
   * The central ingestion CLI (`mail-todo-ingest-knowledge`) converts raw company documents into standard Markdown files committed under `data/extracted/*.md`.
   * **Both Email RAG and AI Chat** query the vector index (Qdrant or Turbovec) built from this shared corpus (`data/extracted/*.md`):
     * **Email RAG (Single-Turn):** Queries `data/extracted/*.md` when an incoming email triggers `RETRIEVE_RAG` intent to gather background context for generating Action Plans.
     * **AI Chat (Multi-Turn):** Queries `data/extracted/*.md` as its **Semantic Memory** (Type 4 Memory) to answer user questions using company knowledge.

2. **Project-Scoped User Documents (Decoupled Runtime Ingestion - ADR-007):**
   * **No**, user-uploaded workspace documents do not pass through the central CLI pipeline.
   * User documents are uploaded via `POST /v1/projects/{id}/documents` and extracted at runtime via [`ProjectDocumentExtractor`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/project_documents.py).
   * These are isolated per user project workspace and used **only in AI Chat** (gated by `USER_DOCUMENTS_ENABLED`).

3. **Emails are Ephemeral and NEVER Ingested (ADR-003):**
   * Raw email bodies and email attachments are ephemeral data.
   * Per security rules, emails are processed only in transient memory during an email evaluation turn and are **never** passed into the document ingestion pipeline or persisted in vector stores.

4. **Vector Store Provider Independence (Turbovec `.tvim` vs Qdrant):**
   * **Yes, Turbovec's `.tvim` file and Qdrant's vector collection are completely independent from each other.**
   * They are two alternative implementations of the `SemanticMemoryPort` ([`bootstrap.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/bootstrap.py)):
     * If `RAG_STORE_PROVIDER=turbovec`: The app builds an in-process, 4-bit quantized vector index stored locally at `.data/turbovec_index.tvim` ([`turbovec_memory.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py)).
     * If `RAG_STORE_PROVIDER` is unset or default (with `QDRANT_ENABLED=true`): The app connects to an external or local Qdrant vector database server collection ([`qdrant.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/qdrant.py)).
   * Both stores read from the **same source Markdown files** (`data/extracted/*.md`), but they index and store the text/vector embeddings in completely separate, independent formats. At runtime, the app selects **one active provider** via `RAG_STORE_PROVIDER`, and both Email RAG and AI Chat query that active provider.

---

## 2. Ingestion Pipeline Execution Flow (Deterministic Batch Phase)

The central Document Ingestion Pipeline ([`KnowledgeIngestionService`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py)) is a **pure extraction and normalization pipeline**. It does **not** run LLMs or Intent Classifiers during document processing.

```mermaid
flowchart TB
    CLI["CLI Entrypoint<br/>(mail-todo-ingest-knowledge)"] --> STAGE1
    
    subgraph STAGE1["Stage 1: Discovery & Security Validation"]
        DIR["Directory & Path Guard"] --> SYM{"Check Symlinks"}
        SYM -->|Symlink Found| REJECT1["Fail: symlink_not_allowed"]
        SYM -->|Valid File| SLUG{"Slug Normalization"}
        SLUG -->|Collision| REJECT2["Fail: output_name_collision"]
    end

    SLUG -->|Valid Slug| STAGE2

    subgraph STAGE2["Stage 2: Idempotent Hash Gating"]
        HASH["Calculate SHA-256 Fingerprint"] --> MANIFEST{"Query ingestion-manifest.json"}
        MANIFEST -->|Hash Matches & Not Forced| SKIP["Skip Processing<br/>(outcome='skipped')"]
    end

    MANIFEST -->|New or Changed File| STAGE3

    subgraph STAGE3["Stage 3: Format Extraction & Text Normalization"]
        TYPE{"File Format"}
        TYPE -->|.docx| DOCX["DocxExtractor<br/>(OpenXML AST to Markdown headings & tables)"]
        TYPE -->|.pdf| PDF["PdfInspector<br/>(Text vs Scanned check + <!-- Page N --> markers)"]
        PDF -->|Scanned PDF| OCR_CHECK{"OCR Configured?"}
        OCR_CHECK -->|No| FAIL_OCR["Fail: mistral_not_configured"]
    end

    subgraph STAGE4["Stage 4: Atomic Persistence & Vector Indexing"]
        DOCX --> ATOMIC["Write to *.tmp and rename to data/extracted/*.md"]
        PDF -->|Native Text PDF| ATOMIC
        ATOMIC --> REGISTER["Update ingestion-manifest.json"]
        REGISTER --> VECTOR["Vector Store Ingestion<br/>(load_corpus into Turbovec / Qdrant)"]
    end
```

### Ubiquitous Language & Terminology Reference (Beginner Guide)

If you are new to this codebase, use this table to decode all domain terms, acronyms, and file concepts used throughout the ingestion pipeline:

| Domain Term / Concept | Simple Plain-English Explanation | Code / File Reference | Why It Matters |
| :--- | :--- | :--- | :--- |
| **Ingestion Pipeline** | The offline automated assembly line that converts raw documents (`.pdf`, `.docx`) into standardized Markdown files (`data/extracted/*.md`). | [`service.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py) | Ensures all raw company documents are converted into uniform, clean text that vector search engines can easily read. |
| **Corpus (`data/extracted/*.md`)** | The ground-truth collection of converted Markdown files stored on disk. | `data/extracted/` | This is the single source of truth for all enterprise knowledge used by both Email RAG and AI Chat. |
| **Manifest Store (`ingestion-manifest.json`)** | A tracking ledger/database stored as JSON that records file hashes, timestamps, page counts, and extraction results. | [`manifest.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/manifest.py) | Remembers what files were already ingested so the system doesn't waste time/money re-processing unchanged files. |
| **Idempotency / Idempotent Hash Gating** | Running the ingestion script 1 time or 100 times on the same input files produces the exact same result without duplicate work. | `ManifestStore.should_skip()` in [`manifest.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/manifest.py) | Calculates a SHA-256 fingerprint for each file; if the fingerprint matches the manifest, it skips extraction immediately. |
| **Slug Normalization** | Transforming complex file names like `"Company Policy (v2) Final!.pdf"` into clean ASCII filenames like `"company-policy-v2-final.md"`. | `_output_name()` in [`service.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py) | Prevents invalid filename characters, spaces, or OS path issues across Windows, Linux, and macOS. |
| **Slug Collision (`output_name_collision`)** | An error that occurs if two different input files slugify into the exact same output filename (e.g. `Policy-A.docx` and `Policy_A.docx` both becoming `policy-a.md`). | [`models.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/models.py) | Prevents one file from accidentally overwriting another file's content in the extracted corpus. |
| **Symlink Defense (`symlink_not_allowed`)** | Rejecting shortcut/symbolic link files found in input folders. | [`service.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py) | Security rule: prevents malicious users from placing a symlink pointing to sensitive system files (like `/etc/passwd`). |
| **Atomic Write (`.tmp` $\rightarrow$ `.md`)** | Writing Markdown output to a temporary `.tmp` file first, then atomically renaming it to `.md` in a single instant OS operation (`os.replace`). | `write_markdown_atomically()` in [`manifest.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/manifest.py) | Guarantees that vector indexers running in parallel will never read a half-written, corrupted file while ingestion is active. |
| **DOCX AST Parsing** | Converting Word XML documents into structured Markdown headings (`#`), lists (`-`), and pipe tables (`\| Header \|`). | [`docx_extractor.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/docx_extractor.py) | Preserves Word document structure (headings, tables) cleanly in text without needing heavy Word software. |
| **PDF Inspection & Page Markers** | Scanning PDF pages to check if text is native/searchable and inserting comments like `<!-- Page 3 -->` into the output Markdown. | [`pdf_inspector.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/pdf_inspector.py) | Allows vector search and LLM citations to refer back to exact PDF page numbers (`[Doc Name, Page 3]`). |
| **Scanned PDF / OCR Deferral (`mistral_not_configured`)** | Halting PDF ingestion gracefully if a PDF contains scanned image pages instead of native text, when OCR is not enabled. | [`pdf_inspector.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/pdf_inspector.py) | Prevents writing empty/garbage text to the knowledge corpus when text extraction fails on scanned images. |
| **Knowledge Chunk (`KnowledgeChunk`)** | Small, structured chunks of text (with headers and page metadata) sliced out of Markdown files for vector storage. | [`knowledge_base.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py) | LLMs cannot digest entire 500-page books at once; chunking breaks documents into digestible 500-token pieces. |
| **Vector Store / Semantic Store (Qdrant & Turbovec)** | Databases that convert text chunks into mathematical numbers (vectors) to allow semantic search by meaning rather than exact keywords. | [`qdrant.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/qdrant.py) & [`turbovec_memory.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py) | Enables asking questions in natural language (e.g. "What is the policy for sick leave?") and getting relevant document sections. |
| **Ephemeral Envelope (`EphemeralEmailEnvelope`)** | A temporary in-memory object holding email text and attachments during a single execution turn. | [`models.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/domain/models.py) | Raw emails are discarded after processing and never saved into the vector store or long-term knowledge base. |
| **Intent Classifier** | An automated router (`routing.py` for Email, `service.py` for Chat) that analyzes incoming user input to decide what action to take. | [`routing.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/email_action_plan/routing.py) | Saves cost and latency by deciding if RAG vector search is needed or if the prompt can be answered directly without search. |

---

## 3. End-to-End System Separation: Ingestion vs Runtime Execution

To clearly understand where the **Intent Classifier** and **LLM** sit relative to the **Ingestion Pipeline**:

| Phase | Subsystem | Responsibility | Component / Implementation |
| :--- | :--- | :--- | :--- |
| **Offline / Batch** | **Document Ingestion Pipeline** | Converts `.docx`/`.pdf` files into `data/extracted/*.md` and populates vector stores. | [`ingestion_cli.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/ingestion_cli.py) & [`service.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py) |
| **Runtime Request** | **Email Intent Classifier** | Evaluates incoming Gmail envelope to decide `NO_ACTION`, `DIRECT_PLAN`, or `RETRIEVE_RAG`. | [`routing.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/email_action_plan/routing.py) |
| **Runtime Request** | **Chat Intent Classifier** | Determines tool relevance, user document query eligibility, and semantic memory needs. | [`service.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/intent/service.py) |
| **Runtime Execution** | **Vector Store Retrieval** | Fetches top matching knowledge chunks when retrieval is triggered. | [`knowledge_base.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py) / [`qdrant.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/qdrant.py) / [`turbovec_memory.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py) |
| **Runtime Generation**| **LLM Action / Reply Generator** | Takes retrieved chunks + prompt to generate structured Action Plans or multi-turn chat replies. | [`workflow.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/email_action_plan/workflow.py) & [`controller.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/controller.py) |

### End-to-End Runtime Dataflow Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    actor User/Gmail
    participant Ingestion as Knowledge Ingestion Pipeline
    participant Manifest as ingestion-manifest.json
    participant Markdown as data/extracted/*.md
    participant VectorDB as Vector Store (Qdrant / Turbovec)
    participant Classifier as Intent Classifier (Routing)
    participant LLM as LLM Provider (Gemini / Groq)

    note over Admin, VectorDB: Phase 1: Offline Knowledge Ingestion (Deterministic Batch)
    Admin->>Ingestion: mail-todo-ingest-knowledge --source data/raw
    Ingestion->>Manifest: Check SHA-256 hash (skip if unmodified)
    Ingestion->>Markdown: Write normalized Markdown atomically (*.tmp -> *.md)
    Ingestion->>Manifest: Update hash, status, page count
    Ingestion->>VectorDB: Chunk & embed Markdown into vector store

    note over User/Gmail, LLM: Phase 2: Runtime Email & Chat Execution
    User/Gmail->>Classifier: Inbound Email or Chat Message
    Classifier->>Classifier: Classify Intent (NO_ACTION | DIRECT_PLAN | RETRIEVE_RAG)
    alt Intent requires RAG Retrieval
        Classifier->>VectorDB: Query relevant knowledge chunks
        VectorDB-->>Classifier: Return top KnowledgeChunk matches
    end
    Classifier->>LLM: Send Prompt + Retrieved Context Chunks
    LLM-->>User/Gmail: Return Structured Action Plan or Chat Response
```

---

## 4. Key Subsystem Comparison

| Metric / Dimension | Central Knowledge Ingestion | Project User Documents (ADR-007) | Email Action Plan RAG (ADR-003) | AI Chat Subsystem (ADR-004) |
| :--- | :--- | :--- | :--- | :--- |
| **Primary Scope** | Enterprise Company Docs | User Workspace Files | Single-turn Gmail Action Plans | Multi-turn AI Assistant |
| **Ingestion Entrypoint** | CLI `mail-todo-ingest-knowledge` | API `POST /v1/projects/{id}/documents` | Ephemeral Mailbox Fetch | SSE Endpoint `/v1/cowork/chat` |
| **Storage Destination** | `data/extracted/*.md` + Shared Vector DB | Project-scoped Vector Index | SQLite/Postgres Action Tasks | Session Buffer, Profile, Episodic, Semantic |
| **Uses Ingestion Pipeline?** | **Yes** (Primary pipeline) | **Partial** (Uses `ProjectDocumentExtractor`) | **No** (Reads vector store downstream) | **No** (Reads vector store downstream) |
| **Uses Intent Classifier?** | No | No | **Yes** (`routing.py`) | **Yes** (`service.py`) |

---

## 5. Summary Cheat-Sheet for Developers

1. **Document Ingestion** converts raw company files (`.docx`, `.pdf`) into clean Markdown (`data/extracted/*.md`) and populates the vector store offline.
2. **Email RAG** and **AI Chat** are two separate workflows, but they **both read from the same vector store** created by the ingestion pipeline.
3. **Emails are never ingested into the RAG corpus** (ephemeral envelope only).
4. **User Documents in Chat** use project-scoped runtime extraction ([`project_documents.py`](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/project_documents.py)) decoupled from the central company knowledge CLI.
5. **Intent Classification and LLMs** happen at runtime when emails or chat messages arrive, querying the vector store populated by the ingestion pipeline.

---

## 6. Architectural Trade-Off Analysis (Turbovec vs. Qdrant)

When choosing between **Turbovec (In-Process 4-Bit Snapshot)** and **Qdrant (External Vector DB)** via `RAG_STORE_PROVIDER`, there are distinct performance, operational, and complexity trade-offs:

### Detailed Trade-Off Comparison

| Metric / Dimension | **Turbovec 4-Bit Snapshot (`.tvim`)** | **Qdrant Vector DB (Server)** | Trade-Off Rationale |
| :--- | :--- | :--- | :--- |
| **Infrastructure Overhead** | **Zero** (Pure in-process C++/Python) | Requires Docker container or cloud cluster (Port 6333) | Turbovec requires zero external server setup or Ops management. |
| **Startup / Boot Speed** | **Instant (< 5 milliseconds)** | Network connection + gRPC handshake delay | Turbovec loads local `.tvim` snapshot file immediately on server startup with 0 API calls. |
| **RAM Footprint** | **~3 MB RAM** (75% lower memory) | Depends on Qdrant server RAM | 4-bit quantization reduces memory per vector from 4.096 KB to 0.512 KB. |
| **Retrieval Accuracy** | **88.64% Recall@5** (99.6% precision match) | Exact 32-bit Float Precision | Microscopic loss of float precision ($2^4 = 16$ quantization buckets per coordinate vs 32-bit floats). |
| **Scaling & Multi-Tenancy**| Single-server node / local MVP | **Cloud-native horizontal scaling** | Qdrant supports multi-node clusters, dynamic ACL payload filtering, and dynamic multi-tenant CRUD. |
| **Corpus Updates** | Re-quantizes and writes snapshot file to disk | Real-time REST/gRPC document upserts | Qdrant handles frequent individual document writes better without rewriting snapshot files. |

### Summary Recommendation Matrix

* **Choose Turbovec (`RAG_STORE_PROVIDER=turbovec`) when:**
  * Running locally, on edge servers, desktop apps, or single-server MVPs where setting up a Docker container for Qdrant is overkill.
  * You need instant app boot times (< 5 ms) and minimal RAM usage (~3 MB RAM).
* **Choose Qdrant (`QDRANT_ENABLED=true`) when:**
  * Deploying to multi-tenant production cloud environments requiring dynamic multi-user document CRUD, payload ACL filtering, and horizontal database scaling.

