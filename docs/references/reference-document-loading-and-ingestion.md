# Document Loading and Ingestion Pipeline

## Fresher AI Engineer Key Concepts

> [!NOTE]
> **Why is Ingestion the Foundation of RAG?**  
> An LLM is only as good as the context provided to it. The ingestion pipeline transforms raw unstructured documents (PDF, DOCX, Markdown, Text) into clean, versioned, section-aware chunks stored in a vector database.

## Overview

The ingestion pipeline handles raw file upload, content hashing, document parsing, deterministic chunking, optional LLM contextual enrichment, vector embedding, and persistence to both Qdrant and the local document registry.

## Pipeline Architecture

```mermaid
flowchart LR
    File[Raw Upload Bytes] --> Hash[Compute SHA-256 Digest]
    Hash --> RegistryCheck{Check Registry}
    RegistryCheck -->|New File| Parse[Parse Text & Format]
    RegistryCheck -->|Same Hash & No Force| Skip[Return Existing Document]
    RegistryCheck -->|Updated Hash / Force| Parse

    Parse --> StatusCheck{Text Extracted?}
    StatusCheck -->|Yes| Chunk[Section-Aware Chunker]
    StatusCheck -->|PDF No Text| OCR[Status: needs_ocr]
    StatusCheck -->|Failed| Fail[Status: failed]

    Chunk --> Enrich{Enable Enrichment?}
    Enrich -->|Yes| LLMEnrich[Generate Summary & Qs]
    Enrich -->|No| WriteDisk[Write Source Bytes to Disk]
    LLMEnrich --> WriteDisk
    WriteDisk --> Store[Embed Vectors & Save to Qdrant]

    Store --> SaveReg[Upsert Document Registry]
```

## Key Ingestion Steps

### 1. Versioning & SHA-256 De-duplication (`src/ingestion/service.py`)
- Every uploaded document undergoes SHA-256 hashing.
- If a document with the same source filename and hash exists, ingestion returns the existing document without reprocessing unless `force=True`.
- If the content hash changes, the document version increments (e.g. `v1` -> `v2`), and prior chunk versions are safely pruned from Qdrant.

### Evaluation ingestion and coordinate recovery

The staged evaluator keeps the raw DOCX and the canonical Markdown source as
separate identities. `data/raw/01_2021_ND-CP_283247.docx` is the opt-in
ingestion input; `data/extracted/01_2021_ND-CP_283247.md` is the stable source
used to validate golden excerpts and their legal `doc_id`, chapter, and article
coordinates. Running `rag-eval ingest --source data/raw/01_2021_ND-CP_283247.docx`
is idempotent for unchanged bytes; `--force-reingest` is permitted only when a
source is supplied.

Legacy Qdrant points that lack compatible canonical coordinates cannot be used
for staged retrieval. After wiping the local or dev collection, re-index from
the retained raw DOCX with
`rag-eval ingest --source data/raw/01_2021_ND-CP_283247.docx --force-reingest`.
The force flag is required when the registry still records the same source
bytes; otherwise idempotent ingestion skips the empty replacement index. Then
use the staged evaluator's preflight instead of treating old payloads as
equivalent. See the [evaluation operations contract](05-observability-evaluation-and-operations.md#staged-offline-rag-evaluation)
for the exact recovery and verification sequence.

### 2. Document Parsing (`src/ingestion/parser.py`)
- Supports `.md`, `.txt`, `.pdf`, and `.docx` formats with two extraction modes (`EXTRACTION_MODE` in `src/settings.py`):
  - **Simple Mode** (`simple`, default): Parses files locally. PDF parsing uses PyMuPDF (`pymupdf` / `pymupdf4llm`) for layout-aware markdown extraction (preserving tables and headings). DOCX parsing uses `python-docx`, mapping Word heading styles to markdown headings and table rows to pipe-separated lines. PDF files without an extractable text layer in simple mode are marked with `status = DocumentStatus.NEEDS_OCR`.
  - **Advanced Mode** (`advanced`): Uses OCR via `MistralOcrExtractor` (`mistral-ocr-latest` in `src/ingestion/ocr.py`, requiring `MISTRAL_API_KEY`). Routes PDF and DOCX documents to the Mistral OCR API for advanced extraction, handling OOXML zip repacking and saving figure images to `data/extracted/images`.
- Extracted text is normalized to UTF-8.

### 3. Section-Aware Chunking (`src/ingestion/chunker.py`)
- Documents are split into logical chunks based on headings and paragraph boundaries (target size 350 tokens / ~1,078 characters).
- Each chunk preserves section metadata, position indices, and legal source coordinates (`doc_id`, `chapter`, `article` via `src/ingestion/structure.py`) to maintain logical context during retrieval.

### 4. Optional Contextual LLM Enrichment (`src/ingestion/enrichment.py`)
- When `ENABLE_ENRICHMENT=true` in `settings.py`, an LLM pass returns a validated `ChunkEnrichment` object (instructor-backed, so no hand-rolled JSON parsing) containing:
  1. A concise chunk summary.
  2. Hypothetical user questions answered by the chunk (capped at 5).
  3. A contextual prefix and key/value metadata labels.
- The contextual prefix is prepended to `retrieval_text`, dramatically improving dense vector retrieval hit rates for abstract questions.

### 5. Vector Store Upsert (`src/retrieval/qdrant_store.py`)
- Chunks are embedded using Jina (`jina-embeddings-v5-omni-small`, task `retrieval.passage`, with Gemini `RETRIEVAL_DOCUMENT` fallback) into 1024-dimensional vectors.
- Payload objects containing chunk text, source name, status (`ready`), document ID, version, position, section, and source coordinates (`doc_id`, `chapter`, `article`) are written to Qdrant.
