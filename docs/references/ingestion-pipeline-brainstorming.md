# Ingestion Pipeline Brainstorming & Architecture Reference Board

**Status:** Reference, Study Guide & Optimization Blueprint  
**Location:** `docs/references/ingestion-pipeline-brainstorming.md`  
**Primary Theoretical Reference:** [Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) (Phase 1: Indexing & Document Loading)  
**Related Architecture Docs:**  
- [06-knowledge-and-document-ingestion-pipeline.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/06-knowledge-and-document-ingestion-pipeline.md)
- [01-email-action-plan-and-rag.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/01-email-action-plan-and-rag.md)
- [02-ai-chat-and-typed-memory.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/02-ai-chat-and-typed-memory.md)
- [05-rag-architecture.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/05-rag-architecture.md)

---

## 1. Core Conceptual Foundations & Boundary Invariants

### Q: Is the ingestion pipeline a centralized document ingestion pipeline for Email RAG and Chat with PRD (including LLM + Intent classifier and RAG)? Do they both use the same ingestion pipeline?

### Key Architectural Answers

1. **Shared Company Knowledge Base (Centralized Ingestion):**
   * **Yes**, for enterprise administrator documentation (`.docx`, `.pdf`, `.txt`, `.md`).
   * The central ingestion CLI (`mail-todo-ingest-knowledge`) converts raw company documents into standard Markdown files committed under `data/extracted/*.md`.
   * **Both Email RAG and AI Chat** query the vector index (Qdrant or Turbovec) built from this shared corpus (`data/extracted/*.md`):
     * **Email RAG (Single-Turn):** Queries `data/extracted/*.md` when an incoming email triggers `RETRIEVE_RAG` intent to gather background context for generating Action Plans.
     * **AI Chat (Multi-Turn):** Queries `data/extracted/*.md` as its **Semantic Memory** (Type 4 Memory) to answer user questions using company knowledge.

2. **Project-Scoped User Documents (Decoupled Runtime Ingestion - ADR-007):**
   * **No**, user-uploaded workspace documents do not pass through the central CLI pipeline.
   * User documents are uploaded via `POST /v1/projects/{id}/documents` and extracted at runtime via [`ProjectDocumentExtractor`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/project_documents.py).
   * These are isolated per user project workspace and used **only in AI Chat** (gated by `USER_DOCUMENTS_ENABLED`).

3. **Emails are Ephemeral and NEVER Ingested (ADR-003):**
   * Raw email bodies and email attachments are ephemeral data.
   * Per security rules, emails are processed only in transient memory during an email evaluation turn and are **never** passed into the document ingestion pipeline or persisted in vector stores.

4. **Vector Store Provider Independence (Turbovec `.tvim` vs Qdrant):**
   * **Turbovec's `.tvim` file and Qdrant's vector collection are completely independent from each other.**
   * They are two alternative implementations of the `SemanticMemoryPort` ([`bootstrap.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/bootstrap.py)):
     * If `RAG_STORE_PROVIDER=turbovec`: The app builds an in-process, 4-bit quantized vector index stored locally at `.data/turbovec_index.tvim` ([`turbovec_memory.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py)).
     * If `RAG_STORE_PROVIDER` is unset or default (with `QDRANT_ENABLED=true`): The app connects to an external or local Qdrant vector database server collection ([`qdrant.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/qdrant.py)).
   * Both stores read from the **same source Markdown files** (`data/extracted/*.md`), but they index and store the text/vector embeddings in completely separate, independent formats. At runtime, the app selects **one active provider** via `RAG_STORE_PROVIDER`, and both Email RAG and AI Chat query that active provider.

---

## 2. Ingestion Pipeline Execution Flow (Deterministic Batch Phase)

The central Document Ingestion Pipeline ([`KnowledgeIngestionService`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py)) is a **pure extraction and normalization pipeline**. It does **not** run LLMs or Intent Classifiers during document processing.

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

    subgraph STAGE3["Stage 3: Format Extraction, Text Sanitization & Metadata Harvesting"]
        MODE{"Extraction Mode<br/>(EXTRACTION_MODE)"}
        MODE -->|"adaptive (default)"| TYPE{"File Format Router"}
        MODE -->|"advance"| OCR["MistralOcrExtractor<br/>(mistral-ocr-latest)"]
        OCR --> FIGURES["Extract Figures to data/extracted/images/"]

        TYPE -->|".docx"| DOCX["DocxExtractor<br/>(OpenXML AST to Markdown headings & tables)"]
        TYPE -->|".pdf"| PDF["PdfInspector<br/>(Text vs Scanned Inspection)"]
        TYPE -->|".txt / .md"| TXT["TextExtractor<br/>(Direct Read & Encoding Validation)"]
        PDF -->|"Native Text PDF"| RENDER_PDF["PDF Page Renderer"]
        PDF -->|"Scanned / OCR Needed"| OCR_CHECK{"MISTRAL_API_KEY?"}
        OCR_CHECK -->|"Configured"| OCR
        OCR_CHECK -->|"Missing"| FAIL_OCR["Fail: mistral_not_configured"]

        DOCX --> CLEAN["Text Sanitization Engine<br/>(Unicode NFC, Whitespace, Control Char Strip)"]
        RENDER_PDF --> CLEAN
        TXT --> CLEAN
        OCR --> CLEAN
        CLEAN --> META["Metadata Harvester & Frontmatter Generator<br/>(doc_id, title, page_count, timestamp, category)"]
    end

    subgraph STAGE4["Stage 4: Atomic Persistence & Vector Store Commit"]
        META --> ATOMIC["Write to *.tmp and rename to data/extracted/*.md"]
        FIGURES --> ATOMIC
        ATOMIC --> REGISTER["Update ingestion-manifest.json"]
        REGISTER --> VECTOR["Vector Store Ingestion<br/>(load_corpus into Turbovec / Qdrant)"]
    end
```

---

## 3. Ubiquitous Language & Terminology Reference

| Domain Term / Concept | Simple Plain-English Explanation | Code / File Reference | Why It Matters |
| :--- | :--- | :--- | :--- |
| **Ingestion Pipeline** | The offline automated assembly line that converts raw documents (`.pdf`, `.docx`, `.txt`) into standardized Markdown files (`data/extracted/*.md`). | [`service.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py) | Ensures all raw company documents are converted into uniform, clean text that vector search engines can easily read. |
| **Document Loading** | The foundational Phase 1 ETL step in RAG: acquiring raw files, parsing layouts, cleaning noisy text, and harvesting metadata. | [Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) §II.2.1 | Garbage in = Garbage out. Clean text & rich metadata are the prerequisite for high-precision retrieval. |
| **Corpus (`data/extracted/*.md`)** | The ground-truth collection of converted Markdown files stored on disk. | `data/extracted/` | Single source of truth for all enterprise knowledge used by both Email RAG and AI Chat. |
| **Manifest Store (`ingestion-manifest.json`)** | A tracking ledger that records file hashes, timestamps, page counts, extractor types, and metadata. | [`manifest.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/manifest.py) | Remembers what files were already ingested so the system skips unchanged files instantly. |
| **Idempotency** | Running the ingestion script 1 time or 100 times produces identical results without duplicate work. | `ManifestStore.should_skip()` in [`manifest.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/manifest.py) | Calculates SHA-256 fingerprints; matching hashes skip extraction immediately. |
| **Unicode Normalization (NFC)** | Converting composite Unicode characters (combining accents) into standard precomposed NFC forms. | `clean_vietnamese_text` ([Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) §IV.3) | Critical for Vietnamese/multilingual text: prevents embedding and BM25 token mismatch bugs. |
| **Pre-Filtering (Metadata Filtering)** | Using structured metadata (e.g. `year: 2024`, `category: legal`, `doc_id`) to narrow search space before vector ranking. | [Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) §II.2.1 | Avoids searching entire database when user asks about a specific scope (e.g. "Doanh thu năm 2024"). |
| **Atomic Write (`.tmp` $\rightarrow$ `.md`)** | Writing Markdown output to a temporary `.tmp` file first, then atomically renaming it via `os.replace`. | `write_markdown_atomically()` in [`manifest.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/manifest.py) | Guarantees vector indexers running in parallel never read half-written, corrupted files. |
| **PDF Inspection & Page Markers** | Scanning PDF structure to check if text is native/searchable and inserting `<!-- Page N -->` markers. | [`pdf_inspector.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/pdf_inspector.py) | Allows vector search and LLM citations to refer back to exact source page numbers (`[Doc, Page 3]`). |
| **Knowledge Chunk (`KnowledgeChunk`)** | Section-bounded, page-tagged slices of text indexed into vector stores. | [`knowledge_base.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py) | LLMs cannot digest entire 500-page manuals at once; chunking breaks text into digestible 500-token pieces. |

---

## 4. End-to-End System Separation: Ingestion vs Runtime Execution

| Phase | Subsystem | Responsibility | Component / Implementation |
| :--- | :--- | :--- | :--- |
| **Offline / Batch** | **★ Document Ingestion Pipeline** | Converts `.docx`/`.pdf`/`.txt`/`.md` into `data/extracted/*.md` with **★ YAML Frontmatter** & indexes chunks with **★ page coordinates**. | [`ingestion_cli.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/ingestion_cli.py) & [`service.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py) |
| **Runtime Request** | **Email Intent Classifier** | Evaluates incoming Gmail envelope to decide `NO_ACTION`, `DIRECT_PLAN`, or `RETRIEVE_RAG`. | [`routing.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/email_action_plan/routing.py) |
| **Runtime Request** | **Chat Intent Classifier** | Determines tool relevance, user document query eligibility, and semantic memory needs. | [`service.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/intent/service.py) |
| **Runtime Execution** | **★ Vector Store Retrieval** | Fetches top matching chunks via Dense (Qdrant/Turbovec) + Sparse (BM25) with **★ Dynamic Metadata Pre-Filtering**. | [`knowledge_base.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py) / [`qdrant.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/qdrant.py) / [`turbovec_memory.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py) |
| **Runtime Generation**| **LLM Action / Reply Generator** | Takes retrieved chunks + prompt to generate structured Action Plans or chat replies with **★ page citations**. | [`workflow.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/email_action_plan/workflow.py) & [`controller.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/controller.py) |

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    actor User/Gmail
    participant Ingestion as Knowledge Ingestion Pipeline
    participant Manifest as ingestion-manifest.json
    participant Markdown as ★ data/extracted/*.md (Frontmatter)
    participant VectorDB as ★ Vector Store (Pre-Filtered Index)
    participant Classifier as Intent Classifier (Routing)
    participant LLM as LLM Provider (Gemini / Groq)

    note over Admin, VectorDB: Phase 1: Offline Knowledge Ingestion (Deterministic Batch)
    Admin->>Ingestion: mail-todo-ingest-knowledge --source data/raw
    Ingestion->>Manifest: Check SHA-256 hash (skip if unmodified)
    Ingestion->>Ingestion: ★ Clean text (Unicode NFC, strip noise) & Harvest Metadata
    Ingestion->>Markdown: ★ Write normalized Markdown + YAML Frontmatter (*.tmp -> *.md)
    Ingestion->>Manifest: ★ Update manifest with rich metadata (doc_id, year, category)
    Ingestion->>VectorDB: ★ Index KnowledgeChunks with Page Coordinates & Metadata Payloads

    note over User/Gmail, LLM: Phase 2: Runtime Email & Chat Execution
    User/Gmail->>Classifier: Inbound Email or Chat Message
    Classifier->>Classifier: Classify Intent (NO_ACTION | DIRECT_PLAN | RETRIEVE_RAG)
    alt Intent requires RAG Retrieval
        Classifier->>VectorDB: ★ Query with Metadata Pre-Filtering (scope by year/category)
        VectorDB-->>Classifier: ★ Return top KnowledgeChunks with Page Citations (Page N)
    end
    Classifier->>LLM: Send Prompt + Retrieved Context Chunks & Page Citations
    LLM-->>User/Gmail: Return Structured Action Plan or Grounded Chat Response
```

---

## 5. Documents Loading (Level 2 Architecture: Current System vs Target Architecture)

Document Loading is the foundational Phase 1 ETL gateway in enterprise RAG systems ([Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) §II.2.1). It governs how raw unstructured assets (`.docx`, `.pdf`, `.txt`, `.md`) are discovered, layout-parsed, sanitized, enriched with metadata, and converted into canonical ground-truth artifacts.

In this architecture, Document Loading is split across two strictly decoupled planes:
1. **Company Knowledge Ingestion Plane (Offline / Batch):** Curated administrative documents processed into `data/extracted/*.md` and indexed into Turbovec hybrid for both Email RAG and AI Chat.
2. **Project-Scoped User Document Ingestion Plane (Runtime / Async):** User-uploaded workspace documents processed on-the-fly into Postgres `project_document_chunks` plus a per-project Turbovec `.tvim` (ADR-007 / ADR-008).

---

### 5.1 Current System (As-Is Level 2 Architecture)

In the current implementation ([06-knowledge-and-document-ingestion-pipeline.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/06-knowledge-and-document-ingestion-pipeline.md)), Document Loading is driven by [`KnowledgeIngestionService`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py) (CLI) and [`ProjectDocumentExtractor`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/project_documents.py) (Runtime).

```mermaid
flowchart TB
    SRC["Source Files (.docx, .pdf)"] --> ROUTER{"Format Router"}

    subgraph PARSE_STAGE["1. Layout Extraction Engine"]
        ROUTER -->|".docx"| DOCX["DocxExtractor<br/>• OpenXML headings & tables"]
        ROUTER -->|".pdf"| PDF_INSPECT["PdfInspector<br/>• Native text & page markers"]
        PDF_INSPECT -->|"Scanned / Mixed"| OCR["MistralOcrExtractor<br/>• Cloud OCR fallback"]
    end

    PARSE_STAGE --> WRITE_STAGE

    subgraph WRITE_STAGE["2. Atomic Markdown Persistence"]
        OUTPUT["Raw Markdown<br/>• No frontmatter / No NFC"] --> ATOMIC["Atomic Disk Write<br/>• *.tmp -> data/extracted/*.md"]
        ATOMIC --> LEDGER["ManifestStore<br/>• Update ingestion-manifest.json"]
    end

    subgraph DOWNSTREAM["3. Downstream Corpus Loading & Indexing"]
        CORPUS["data/extracted/*.md"] --> LOAD_CORPUS["load_corpus()<br/>• Reads text & H1 title (ignores Page N)"]
        LOAD_CORPUS --> CHUNKS["KnowledgeChunk Model<br/>• Basic section-split chunks"]
        CHUNKS --> VECTOR_DB["Vector Stores (Turbovec / Qdrant)<br/>• Unfiltered semantic search"]
    end

    WRITE_STAGE --> DOWNSTREAM
```

#### Identified Architectural Inefficiencies in Current System:
1. **No Central Unicode Normalization (NFC):** Extracted text uses basic `.strip()` without Unicode normalization (`unicodedata.normalize("NFC")`), causing embedding and BM25 token mismatch on Vietnamese/multilingual text.
2. **Missing Document Frontmatter & Rich Metadata:** Markdown files are emitted without structured YAML frontmatter (`doc_id`, `title`, `author`, `category`, `year`, `page_count`).
3. **Page Coordinate Disconnection:** `PdfInspector` inserts `<!-- Page N -->` markers, but `load_corpus()` in [`knowledge_base.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py) ignores them, preventing page-level citation boundaries.
4. **Format Restriction:** Whitelist strictly allows `.docx` and `.pdf`, skipping plain text (`.txt`) and pre-existing Markdown (`.md`) files.
5. **Absence of Pre-Filtering Metadata Payloads:** Vector stores index raw chunk text without structured metadata fields, preventing scope-narrowed search.

---

### 5.2 Target Document Loading (To-Be Level 2 Architecture)

The Target Document Loading architecture aligns with [Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) §II.2.1 and [TARGET-ARCHITECTURE.md §3](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md) to establish high layout fidelity, complete text sanitization, rich metadata frontmatter, and end-to-end page coordinate propagation.

```mermaid
flowchart TB
    subgraph INPUTS["Multi-Format Inputs"]
        DOCS["Source Documents<br/>• .docx, .pdf (native/scanned)<br/>• .txt, .md"]
    end

    INPUTS --> EXT_ROUTE{"Format Dispatcher"}

    subgraph PARSERS["1. Multi-Engine Layout Extraction"]
        EXT_ROUTE -->|".docx"| DOCX_PARSER["DocxExtractor<br/>• Heading AST & pipe tables"]
        EXT_ROUTE -->|".pdf"| PDF_ANALYZER["Deterministic PdfInspector<br/>• Native text vs OCR escalation"]
        EXT_ROUTE -->|".txt / .md"| TEXT_PARSER["Text & Markdown Extractor<br/>• Direct UTF-8 ingestion"]

        PDF_ANALYZER -->|"Native Text"| LOCAL_RENDER["Local Page Renderer<br/>• Page coordinates & layout"]
        PDF_ANALYZER -->|"Scanned / Mixed"| MISTRAL_OCR["Mistral OCR API<br/>• Cloud recognition"]
    end

    PARSERS --> SANITIZE

    subgraph SANITIZE["★ 2. Central Text Sanitization Engine"]
        CLEAN["sanitize_text()<br/>• Unicode NFC normalization<br/>• Control char & whitespace cleanup"]
    end

    SANITIZE --> META_ENGINE

    subgraph META_ENGINE["★ 3. Metadata Harvester & Frontmatter Block"]
        FRONTMATTER["YAML Frontmatter Generator<br/>• doc_id, title, category, year, pages"]
        FRONTMATTER --> COMPOSITE["Standardized Markdown<br/>• Frontmatter + Sanitized Body + <!-- Page N -->"]
    end

    META_ENGINE --> PERSIST

    subgraph PERSIST["4. Atomic Storage & Manifest Ledger"]
        ATOMIC_WRITE["Atomic Disk Write & Manifest<br/>• *.tmp -> data/extracted/*.md<br/>• Rich metadata in manifest"]
    end

    PERSIST --> RAG_INDEXING

    subgraph RAG_INDEXING["★ 5. Coordinate-Preserving Indexing & Pre-Filtering"]
        LOADER["load_corpus() Upgrade<br/>• Frontmatter & page markers into chunks"]
        LOADER --> VECTOR_UP["Vector Stores (Turbovec / Qdrant)<br/>• Pre-filtering on year/category/doc_id<br/>• Page-level citation coordinates"]
    end
```

---

### 5.3 Detailed Structural PDF Inspection & Scanned Page Detection Engine

Within the Layout Extraction stage of Document Loading, PDF handling uses a 4-tier deterministic COS inspection pipeline before deciding whether to extract text locally ($0 API cost, <5ms per page) or escalate to cloud OCR:

```mermaid
flowchart TB
    PDF["Input PDF File"] --> INSPECT["PdfInspector (detect_pdf)<br/>Fast COS layer scan"]

    subgraph ANALYSIS["Structural Content Stream Inspection"]
        COS["1. COS Object Graph<br/>• Operator token analysis"]
        COS --> DENSITY["2. Text vs Image Ratio<br/>• >85% bitmap coverage check"]
        DENSITY --> FONTS["3. Font & Unicode Validation<br/>• /ToUnicode CMap mapping"]
    end

    INSPECT --> ANALYSIS

    ANALYSIS --> CLASSIFY{"Classification"}
    CLASSIFY -->|"Native text"| NATIVE["PyMuPDF Native Parser<br/>• Fast offline extraction ($0 cost)"]
    CLASSIFY -->|"Scanned / Mixed"| OCR_ROUTE{"MISTRAL_API_KEY?"}

    OCR_ROUTE -->|"Configured"| MISTRAL["Mistral OCR API<br/>• Cloud extraction"]
    OCR_ROUTE -->|"Missing"| FAIL["Fail Cleanly<br/>• reason_code='mistral_not_configured'"]
```

#### The 4 Deterministic Detection Checks Under the Hood:
1. **Graphic Operator & Token Analysis:** Evaluates explicit string drawing operators (`BT`..`ET`, `Tj`, `TJ`) vs image XObjects (`/Do` `/Image`).
2. **Text Density vs Image Area Coverage Ratio:** If single bitmap image coverage $>85\%$ with negligible text characters, flags page as scanned (`needs_ocr=True`).
3. **Font Encoding & Unicode CMap Mapping:** Verifies glyph-to-Unicode mapping to prevent unreadable replacement glyphs (`\ufffd`).
4. **Invisible OCR / False-Layer Detection:** Detects low-grade OCR artifacts where invisible text misaligns with underlying scanned images.

---

### 5.4 Comprehensive Level 2 Architecture Comparison Matrix

| Architectural Dimension | Current System (As-Is) | Target Document Loading (To-Be) | Key Benefit & Impact |
| :--- | :--- | :--- | :--- |
| **Pipeline Architecture Scope** | Single CLI-driven batch script + basic runtime extractor. | Dual-plane architecture: Curated Company Knowledge Ingestion + Asynchronous Project User Document Plane (ADR-007). | Clear isolation between corporate knowledge and transient user files. |
| **Input Format Whitelist** | Restricted to `.docx` and `.pdf`. | Comprehensive support for `.docx`, `.pdf`, `.txt`, and `.md`. | Enables direct ingestion of markdown guides, legal text dumps, and technical specs. |
| **Text Sanitization Engine** | Basic `.strip()`; no Unicode normalization across parsers. | Central `sanitize_text()` applying Unicode NFC normalization, control character stripping, and whitespace regularization. | Eliminates token mismatch in Vietnamese/multilingual embeddings and BM25 keyword matching. |
| **Structural Layout Fidelity** | Word OpenXML headings (`#..###`) and basic tables; PDF plain text. | High-fidelity AST heading hierarchies, pipe tables (`\| a \| b \|`), bullet lists, and figure extraction. | Dense & sparse retrieval engines preserve table rows and list contexts cleanly. |
| **PDF Inspection & OCR Routing** | Basic PyMuPDF scan with manual/advance Mistral OCR routing. | 4-tier deterministic COS inspection (operators, text density $>85\%$, CMap fonts, invisible OCR) with automated escalation. | Zero API costs for 90%+ digital PDFs; perfect fidelity for scanned documents. |
| **Metadata Generation** | No metadata emitted in Markdown; technical tracking only in JSON manifest. | Standardized YAML Frontmatter emitted in every Markdown artifact (`doc_id`, `title`, `author`, `year`, `category`, `page_count`). | Self-describing corpus enables auditability, reproducible chunking, and search pre-filtering. |
| **Page Coordinate Propagation** | `<!-- Page N -->` inserted into Markdown but stripped/ignored downstream. | End-to-end page propagation: `<!-- Page N -->` parsed into `KnowledgeChunk.page_start` & `page_end`. | LLMs can produce precise page-level citations (`[Doc Title, Page 12]`). |
| **Corpus Loading (`load_corpus`)** | Reads raw text and regex H1 title; creates flat un-attributed chunks. | Frontmatter-aware and page-aware parser loading rich metadata into `KnowledgeChunk` domain models. | Downstream vector stores receive rich metadata payloads alongside chunk text. |
| **Vector Indexing & Pre-Filtering** | Flat chunk text embedding without metadata payload filters. | Payload keyword indexing in Qdrant & Turbovec allowlists supporting pre-filtering by `year`, `category`, and `doc_id`. | Drastically narrows vector search space and eliminates cross-year / cross-topic false positives. |
| **Concurrency & Idempotency** | Atomic write (`*.tmp` $\rightarrow$ `*.md`) and SHA-256 gating in `ingestion-manifest.json`. | Retained atomic write & SHA-256 gating + versioned hash updating with prune safety. | Zero dirty reads by parallel indexers; sub-second skip on unmodified files. |

---

## 6. ★ Document Loading Optimization Deep-Dive (Grounded in `Simple-RAG.pdf`)

The document loading and ingestion pipeline is decomposed into three distinct, decoupled processing states:

#### State 1: Multi-Format Acquisition & Parsing
```mermaid
stateDiagram-v2
    direction TB

    [*] --> S1: Raw File Input (.pdf, .docx, .txt, .md)

    state "1. Multi-Format Acquisition" as S1 {
        direction TB
        P1: [✓] PDF Native & Scanned (PyMuPDF / Mistral OCR)
        P2: [✓] DOCX OpenXML & Tables (Heading AST & Pipes)
        P3: [✓] OOXML Zip Repacking (Content_Types.xml Fix)
        P4: [★] Plain TXT & MD Support (Direct Ingestion)
    }

    S1 --> [*]: Emits Extracted AST / Raw Stream
```

#### State 2: Text Cleaning & Normalization (Sanitization Engine)
```mermaid
stateDiagram-v2
    direction TB

    [*] --> S2: Extracted AST / Raw Stream

    state "★ 2. Text Cleaning & Normalization" as S2 {
        direction TB
        C1: [✗] Unicode NFC Normalization (Accents & Tokenizer Match)
        C2: [✗] Strip Control & Non-Printable Characters
        C3: [✗] Regularize Whitespace & Consecutive Newlines
        C4: [Current Baseline] Basic string .strip() only
    }

    S2 --> [*]: Emits Sanitized Text Buffer
```

#### State 3: Metadata Harvesting & Ground-Truth Artifact Generation
```mermaid
stateDiagram-v2
    direction TB

    [*] --> S3: Sanitized Text Buffer

    state "★ 3. Metadata Harvesting & Frontmatter Synthesis" as S3 {
        direction TB
        M1: [✗] doc_id & Slug Generation
        M2: [✗] Rich YAML Frontmatter Synthesis (doc_id, title, year, category)
        M3: [✗] Page Coordinate Markers (<!-- Page N -->)
        M4: [✗] Manifest Registry Sync (ingestion-manifest.json)
    }

    state "Output Artifact: Canonical Ground-Truth" as Output {
        direction TB
        O1: Clean Standardized Markdown
        O2: Rich YAML Frontmatter Block
        O3: Embedded <!-- Page N --> Coordinates
    }

    S3 --> Output: Atomic Write (*.tmp -> data/extracted/*.md)
    Output --> [*]: Ready for Vector Ingestion (load_corpus)
```

### 6.1 Pillar 1: Multi-Format Parsing & Layout Fidelity

* **What We Already Have (Live / Implemented):**
  - **DOCX AST Preservation ([`docx_extractor.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/docx_extractor.py)):** Converts Word OpenXML paragraph styles into Markdown heading hierarchies (`#`, `##`, `###`), bullet lists (`-`), and pipe tables (`| Col1 | Col2 |`).
  - **PDF Layer Extraction ([`pdf_inspector.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/pdf_inspector.py)):** Fast PyMuPDF text extraction with `<!-- Page N -->` insertion and scanned page detection.
  - **OOXML Repacking ([`ocr.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/ocr.py)):** Normalizes `[Content_Types].xml` positioning in zip archives to prevent 422 errors with cloud OCR.
* **★ What is New / Target Change (Not Yet Implemented):**
  - **Direct `.txt` and `.md` Ingestion:** Broadening central whitelist beyond `.docx` and `.pdf` to ingest existing plain text and Markdown guidelines directly.

---

### 6.2 ★ Pillar 2: Central Text Sanitization Engine (Vietnamese & Multilingual)

* **What We Already Have (Live / Implemented):**
  - Basic Python string `.strip()` applied during extractor regex cleaning without Unicode normalization.
* **★ What is New / Target Change (Not Yet Implemented):**
  - Central `sanitize_text()` utility integrating **Unicode NFC Normalization**, control character stripping, and whitespace regularization ([Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) §IV.3 `clean_vietnamese_text`):

```python
def sanitize_text(text: str) -> str:
    """Normalize Unicode to NFC, remove non-printable characters, and regularize whitespace."""
    # 1. Unicode NFC Normalization (CRITICAL for Vietnamese accents & token matching)
    text = unicodedata.normalize("NFC", text)
    
    # 2. Strip non-printable / control characters while preserving \n and \t
    text = "".join(
        char for char in text 
        if not unicodedata.category(char).startswith("C") or char in "\n\t"
    )
    
    # 3. Regularize whitespace and multiple empty lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
```

> [!IMPORTANT]
> **Why Unicode NFC is a Non-Negotiable Invariant:**  
> In Vietnamese, a character like `ế` can be encoded as a single code point `\u1EBF` (NFC) or as `e` + `\u0302` (circumflex) + `\u0301` (acute) (NFD). If the corpus uses NFD while the user query uses NFC, BPE/WordPiece tokenizers produce completely different token IDs, causing both Dense Embedding cosine similarity and BM25 exact-match scoring to fail drastically!

---

### 6.3 ★ Pillar 3: Metadata Harvesting & Frontmatter Generation

* **What We Already Have (Live / Implemented):**
  - Output files in `data/extracted/*.md` are flat Markdown files starting with `# Document Title`.
  - `ingestion-manifest.json` tracks operational fields only (`source`, `sha256`, `extractor`, `page_count`, `status`).
* **★ What is New / Target Change (Not Yet Implemented):**
  - Emitting structured **YAML Frontmatter** at the top of every generated Markdown file so the corpus is self-describing and audit-ready:

```yaml
---
document_id: "01-2021-nd-cp-283247"
title: "Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp"
source_file: "01_2021_ND-CP_283247.docx"
extractor: "docx"
page_count: 48
created_at: "2026-08-14T05:00:00Z"
category: "legal_regulation"
year: 2021
---
```

---

### 6.4 ★ Pillar 4: Metadata Pre-Filtering & Page Coordinate Propagation

* **What We Already Have (Live / Implemented):**
  - [`load_corpus()`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py) extracts only `title` (via H1 regex) and ignores `<!-- Page N -->` comments.
  - Qdrant payloads filter only on `document_status: ready`.
  - Turbovec `.tvim` uses flat dense + BM25 search without payload allowlisting.
* **★ What is New / Target Change (Not Yet Implemented):**
  - **Page Coordinate Binding:** `load_corpus()` maps `<!-- Page N -->` markers into `KnowledgeChunk.page_start` and `page_end` for exact page-grounded citations (`[Document Title, Page 12]`).
  - **Dynamic Metadata Pre-Filtering:** Exposing filter keys (`year`, `category`, `document_id`) in retrieval requests to prune non-candidate vectors before scoring ([Simple-RAG.pdf](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/Simple-RAG.pdf) §II.2.1):
    1. **Zero False Positives:** Hard filters guarantee out-of-scope documents (e.g. 2021 files for a 2024 query) are never returned.
    2. **Reduced Latency:** Vector DB searches only matching payload subsets.
    3. **Auditable Citations:** LLMs cite exact coordinate metadata.

---

## 7. ★ Codebase Gap Analysis & Target Architecture Comparison

| Dimension | Current EMAIL-AGENT-v1 State | `Simple-RAG.pdf` Best Practice | Target Optimized State |
| :--- | :--- | :--- | :--- |
| **Text Sanitization** | Basic string `.strip()`; no Unicode normalization across DOCX/PDF. | `clean_vietnamese_text`: NFC normalization + control character filter + whitespace collapse. | Central `sanitize_text()` applied across all extractors (`DocxExtractor`, `PdfInspector`, `ProjectDocumentExtractor`). |
| **Supported Formats** | `.docx`, `.pdf` only in central discovery whitelist. | Multi-format inputs (PDF, DOCX, TXT, MD, HTML). | Expand whitelist to support `.txt` and `.md` direct ingestion in central & project extractors. |
| **Metadata in Corpus** | Raw Markdown with `# Heading` only; no frontmatter or document metadata headers. | Rich metadata extraction (`doc_id`, `title`, `year`, `author`, `pages`, `category`). | Emit YAML frontmatter at the top of every `data/extracted/*.md` file. |
| **Page Marker Handling** | `<!-- Page N -->` inserted into Markdown, but ignored by `load_corpus()` in `knowledge_base.py`. | Page coordinates propagated to each chunk for precise citation and retrieval bounds. | `load_corpus()` parses page comments into `KnowledgeChunk.page_start` & `page_end`. |
| **Manifest Metadata** | Technical tracking only (`source`, `sha256`, `status`, `output`, `extractor`, `page_count`). | Rich metadata tracking for pre-filtering and auditability. | Record `title`, `category`, and extracted attributes in `ingestion-manifest.json`. |
| **Pre-Filtering in Vector Stores** | Qdrant filters only on `document_status`; Turbovec has no metadata filter. | Dynamic metadata pre-filtering on `document_id`, `category`, `year`, `page_range`. | Expose metadata filter keys (`document_id`, `category`, `year`) in `SemanticRetrievalRequest.filters`. |

---

## 8. ★ Scope of Impact: Code Files, Documents & Tests Matrix

When implementing the Document Loading optimizations, the following files and modules will be touched:

### A. Documentation & Architecture References
- [docs/references/ingestion-pipeline-brainstorming.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/ingestion-pipeline-brainstorming.md): Main reference board for ingestion design & optimizations.
- [docs/references/reference-document-loading-and-ingestion.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/references/reference-document-loading-and-ingestion.md): Updated with text sanitization formulas and metadata schemas.
- [docs/architectures/current-architectures/06-knowledge-and-document-ingestion-pipeline.md](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/current-architectures/06-knowledge-and-document-ingestion-pipeline.md): Synchronized to reflect text sanitization and frontmatter metadata pipeline.

### B. Ingestion Subsystem Source Files (`src/cowork_agent/integrations/knowledge_ingestion/`)
- [`service.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py):
  - Integrate `sanitize_text()` into pipeline execution.
  - Expand `_SUPPORTED_SUFFIXES` to include `.txt`, `.md`.
  - Coordinate metadata harvesting and frontmatter writing.
- [`docx_extractor.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/docx_extractor.py):
  - Sanitize extracted paragraph/table text with Unicode NFC.
  - Extract document core properties (`title`, `author`, `created`).
- [`pdf_inspector.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/pdf_inspector.py):
  - Sanitize native text pages with Unicode NFC and control character stripping.
  - Extract PDF metadata dictionary (`Title`, `Author`, `CreationDate`).
- [`project_documents.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/project_documents.py):
  - Apply `sanitize_text()` to user-uploaded project documents.
  - Support `.txt` and `.md` file uploads.
- [`models.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/models.py):
  - Define `DocumentMetadata` contract (`title`, `category`, `year`, `author`, `page_count`).
  - Update `ExtractionResult` and `ManifestEntry` to carry metadata attributes.
- [`manifest.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/manifest.py):
  - Update `_ENTRY_FIELDS` and serialization to preserve extracted metadata attributes in `ingestion-manifest.json`.

### C. RAG & Chunking Subsystem Source Files (`src/cowork_agent/integrations/rag/`)
- [`knowledge_base.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py):
  - Parse YAML frontmatter metadata in `load_corpus()`.
  - Parse `<!-- Page N -->` comments to propagate `page_start` and `page_end` into `KnowledgeChunk`.
- [`markdown_chunking.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/markdown_chunking.py):
  - Ensure page numbers and section headers propagate seamlessly across paragraph boundaries.
- [`qdrant.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/qdrant.py):
  - Store metadata payload (`page_start`, `page_end`, `category`, `year`, `document_id`).
  - Create payload keyword indexes for pre-filtering.
- [`turbovec_memory.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/turbovec_memory.py):
  - Add metadata filtering support to candidate allowlist generation.

### D. Verification Test Suites (`tests/`)
- `tests/unit/integrations/knowledge_ingestion/test_service.py`: Unit tests for text sanitization, format expansion, and metadata frontmatter generation.
- `tests/unit/integrations/knowledge_ingestion/test_docx_extractor.py`: Tests for Unicode NFC and table cleaning in Word documents.
- `tests/unit/integrations/knowledge_ingestion/test_pdf_inspector.py`: Tests for PDF text cleaning and page comment integrity.
- `tests/integration/test_knowledge_ingestion_to_rag.py`: End-to-end test verifying ingested Markdown + Frontmatter is parsed and indexed into Qdrant/Turbovec with full metadata.

---

## 9. Actionable Evolution Roadmap

```md
[Phase 1: Ingestion Sanitization & Metadata Engine]
  1.1 Create text_sanitizer utility (Unicode NFC, control char stripping, whitespace collapse).
  1.2 Integrate sanitizer into DocxExtractor, PdfInspector, ProjectDocumentExtractor.
  1.3 Add YAML Frontmatter generation to KnowledgeIngestionService.

[Phase 2: Corpus Loading & Page Coordinate Propagation]
  2.1 Upgrade load_corpus() in knowledge_base.py to parse YAML frontmatter and <!-- Page N --> comments.
  2.2 Attach page_start, page_end, and metadata attributes to KnowledgeChunk domain objects.

[Phase 3: Vector Store Payload & Pre-Filtering]
  3.1 Index metadata fields in QdrantSemanticMemory payload (document_id, category, page_range, year).
  3.2 Add allowlist metadata filtering to TurbovecSemanticMemory.
  3.3 Validate with full test suite: uv run pytest.
```