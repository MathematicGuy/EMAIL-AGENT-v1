# Clarifying Metadata Placement & Document Loading vs Chunking Architecture

> **Reference Guide:** Document Loading, Metadata Lifecycles, Binary Container Extraction, and Chunk Coordinate Binding.  
> **Target Audience:** AI Engineers & System Architects.  
> **Related Implementations:** [`service.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/service.py), [`docx_extractor.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/docx_extractor.py), [`pdf_inspector.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/knowledge_ingestion/pdf_inspector.py), [`knowledge_base.py`](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/knowledge_base.py).

---

### 1. Direct Yes / No Answers

1. **Does the metadata get appended to the top of the `.md` file?**  
   👉 **YES.** Document metadata is written at the very top of each `.md` file as a structured **YAML Frontmatter** block.

2. **Do we use chunking to save/propagate those metadata later?**  
   👉 **YES.** When chunking runs downstream, every resulting text chunk inherits this metadata and carries it into the vector database.

3. **Is metadata related to chunking?**  
   👉 **YES.** Metadata gives each chunk its contextual coordinates (which document it came from, which section, what year, and which exact page numbers it spans).

---

### 2. How Metadata Is Saved (The 2-Stage Lifecycle)

Metadata is saved in **two distinct layers** across the system:

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: INGESTION TIME (Saved to Disk in Markdown)                      │
└──────────────────────────────────────────────────────────────────────────┘
  File: data/extracted/01_2021_nd_cp.md
  
  ---                                  <-- 1. Document-Level Metadata
  document_id: "01-2021-nd-cp-283247"       (Saved in YAML Frontmatter header)
  title: "Nghị định 01/2021/NĐ-CP"
  category: "legal_regulation"
  year: 2021
  page_count: 48
  ---
  
  # Chương I: Quy định chung
  <!-- Page 1 -->                      <-- 2. Page-Level Coordinates
  Nội dung điều 1...                        (Saved as HTML comments in text body)
  <!-- Page 2 -->
  Nội dung điều 2...

                                       │
                                       ▼ (Downstream load_corpus() reads file)

┌──────────────────────────────────────────────────────────────────────────┐
│ STAGE 2: RAG INDEXING TIME (Saved into Vector Store Payload)             │
└──────────────────────────────────────────────────────────────────────────┘
  Vector Store: Qdrant / Turbovec (.tvim)
  
  Point / Chunk ID: "01-2021-nd-cp-283247#3"
  Vector: [0.024, -0.118, 0.452, ...] (1024-dim embedding)
  
  Payload (JSON Metadata attached to Vector):
  {
    "document_id": "01-2021-nd-cp-283247",   <-- Inherited from Frontmatter
    "document_title": "Nghị định 01/2021/NĐ-CP",
    "category": "legal_regulation",
    "year": 2021,
    "section": "Chương I: Quy định chung",    <-- Extracted by Chunker
    "page_start": 1,                         <-- Parsed from <!-- Page N -->
    "page_end": 2,
    "text": "Nội dung trích đoạn điều 1 và điều 2..."
  }
```

---

### 3. How Metadata Relates to Chunking (Why It Is Essential)

When a 50-page document is split into 100 small chunks (~350 tokens each), chunking and metadata interact in 3 critical ways:

#### A. Preventing "Context-Less Orphan Chunks"
If a chunk only contains the text `"Mức phạt tiền từ 10.000.000 VNĐ đến 20.000.000 VNĐ"`, an LLM has no idea what law, what year, or what offense this applies to.  
By inheriting metadata, the chunk knows:
* `document_title`: *"Nghị định 01/2021/NĐ-CP"*
* `year`: `2021`
* `section`: *"Điều 15: Vi phạm quy định về đăng ký kinh doanh"*

#### B. Page Coordinate Binding (`page_start`, `page_end`)
During chunking, the chunker reads the `<!-- Page N -->` markers placed by the document loader. If a paragraph starts on Page 1 and ends on Page 2, the chunker assigns `page_start = 1, page_end = 2`.  
This allows the AI to provide exact source citations: `[Nghị định 01/2021, Page 1-2]`.

#### C. Enabling Metadata Pre-Filtering at Query Time
When a user asks: *"Quy định xử phạt năm 2024"*, the vector database uses the chunk's `year: 2024` metadata payload to filter out old documents before computing vector math. This eliminates false positives and cuts search latency.

---

### 4. Why We Have Metadata at Both Levels & Why Not Extract All in Chunking

> **Core Principle:** We **must** extract metadata in two separate stages because **Document Metadata requires access to the raw file binaries and global file context**, whereas **Chunk Metadata is created dynamically when slicing text**.

Merging them into a single step is an anti-pattern in production AI engineering.

#### The Metadata Hierarchy: Who Owns What?

| Metadata Scope | Attributes | Who Knows This? | Where It Comes From |
| :--- | :--- | :--- | :--- |
| **Document-Level Metadata** (Global / Macro) | `doc_id`, `title`, `author`, `created_at`, `year`, `category`, `total_pages`, `sha256_hash` | **Document Loader** (Phase 1) | Raw file containers (`.pdf` COS stream, `.docx` `core.xml`, file headers). |
| **Chunk-Level Metadata** (Local / Micro) | `chunk_id`, `section_heading`, `page_start`, `page_end`, `token_count`, `chunk_index` | **Chunker** (Phase 2) | Calculated dynamically when slicing Markdown text into ~350-token windows. |

---

### 5. Why We Cannot (and Should Not) Do It All in the Chunking Step

#### Reason 1: File Binary Properties Disappear After Text Extraction
Document properties like Word Author, PDF creation date, digital signatures, and total file SHA-256 hashes live inside the **raw binary file format** (e.g. OpenXML ZIP archive or PDF binary object tree).  
* The **Document Loader** has access to the raw binary file and extraction libraries (`python-docx`, `pymupdf`, `mistral-ocr`).
* The **Chunker** operates purely on **plain text/markdown strings**. It has zero knowledge of the underlying OS file or binary metadata.  
If you don't harvest document metadata during the Loading phase, that global context is permanently lost.

---

#### Reason 2: The RAG Experimentation Principle (Decoupling Heavy I/O from Agile Chunking)
In production AI engineering, **Document Ingestion is slow and expensive; Chunking is fast and cheap.**

```text
[Raw 500-page PDF / OCR] ──(Heavy I/O: 30-60s)──► [data/extracted/*.md + Frontmatter]
                                                             │
                                   ┌─────────────────────────┼─────────────────────────┐
                                   ▼                         ▼                         ▼
                             Chunk Size: 250           Chunk Size: 500          Semantic Chunking
                             (Fast: < 100ms)           (Fast: < 100ms)           (Fast: < 200ms)
```

* **If combined into 1 step:** Every time you want to experiment with a new chunk size (e.g., test 300 tokens vs 500 tokens, test Markdown vs Late Chunking), you would have to re-open, re-parse, and re-OCR hundreds of heavy PDF and DOCX files.
* **With 2 decoupled steps:** Ingestion runs **once** to create the clean Markdown file with YAML Frontmatter. You can then re-chunk, re-test, and re-embed the corpus in milliseconds without ever touching the raw source files again!

---

#### Reason 3: Chunk Coordinates Depend on Document Pre-Processing
Chunk-level coordinates (`page_start`, `page_end`) cannot exist without the Document Loader's pre-processing:
1. The **Document Loader** inspects the physical PDF pages and injects structural coordinates (`<!-- Page 1 -->`, `<!-- Page 2 -->`).
2. The **Chunker** then parses those coordinate tags to determine that *Chunk #5* spans from *Page 2 to Page 3*.

If the Document Loader didn't preserve and annotate page boundaries in Step 1, the Chunker in Step 2 would have no concept of physical pages.

---

#### Reason 4: Human Auditability & Reproducibility
Having Document Loading emit standard `.md` files with **YAML Frontmatter** creates an intermediate, version-controlled **Source of Truth** on disk:
* A software engineer or compliance auditor can open `data/extracted/01_2021_nd_cp.md` in GitHub/VS Code, read the metadata header, verify the text accuracy, and spot parsing errors immediately.
* If everything went straight from raw PDF into opaque vector embeddings in one black-box step, debugging why an LLM hallucinated or why metadata was corrupted would be a nightmare.

---

### 6. Deep-Dive: Why Document Metadata Requires Access to Raw File Binaries & Global Context

#### 1. The Physical Reality: Documents Are Binary Containers

When you have a `.docx` or `.pdf` file, it is **not** just a stream of text. It is a complex binary structure that contains hidden metadata tables:

##### A. A `.docx` File Is Actually a Zipped Binary Archive
If you change `contract.docx` to `contract.zip` and unzip it, you will find internal XML metadata files like `docProps/core.xml`:

```xml
<!-- Inside contract.docx -> docProps/core.xml -->
<cp:coreProperties>
    <dc:title>Quy Chế Tài Chính Doanh Nghiệp 2024</dc:title>
    <dc:creator>Bộ Tài Chính</dc:creator>
    <dcterms:created>2024-01-15T08:30:00Z</dcterms:created>
    <cp:revision>4</cp:revision>
    <cp:category>Internal Regulation</cp:category>
</cp:coreProperties>
```

##### B. A `.pdf` File Has an Internal Binary Object Dictionary
Inside the binary COS layer of a PDF, Adobe embeds an `/Info` catalog table:

```text
% Inside the binary PDF byte stream:
12 0 obj
<<
  /Title (Nghị định 01/2021/NĐ-CP)
  /Author (Chính Phủ Việt Nam)
  /CreationDate (D:20210104100000Z)
  /PageCount 48
  /Producer (Adobe PDF Library 15.0)
>>
endobj
```

---

#### 2. What Happens When Text Is Extracted?

When the Document Loader extracts text from the binary file into Python string memory, **the binary container is discarded**:

```python
# During Document Loading:
raw_bytes = open("contract.docx", "rb").read()  # Contains XML headers, Author, Created date

# After Text Extraction:
extracted_text = """
# Điều 1: Phạm vi áp dụng
Quy chế này áp dụng cho toàn bộ nhân viên...
"""
```

Notice what just happened:
* `extracted_text` in Python memory is now just a **plain string of characters**.
* It **no longer contains** the author `"Bộ Tài Chính"`, creation date `2024-01-15`, file size, SHA-256 fingerprint, or the total page count!

---

#### 3. Why the Chunker Cannot Access This Binary Metadata

The Chunker's job in Python is purely mathematical and linguistic:
```python
def chunk_markdown(text: str, chunk_size: int = 350) -> list[str]:
    # It takes in a string and splits it by spaces/paragraphs.
    # It has NO access to the original .docx or .pdf file on disk!
```

If you wanted the Chunker to extract this metadata:
1. You would have to pass the **raw binary file** into the chunker.
2. The chunker would have to re-open the `.docx`/`.pdf`, unzip the XML, parse the PDF byte dictionary, and re-run OCR every time it chunks.
3. This creates **tight coupling** and violates the **Single Responsibility Principle (SRP)**: the chunker's job is text splitting, not file parsing.

---

#### 4. What Is "Global Context"?

"Global Context" means properties that apply to the **entire document as a whole**, not just a 300-token text slice:

| Global Context (Whole Document) | Why it must be extracted at Document Load time |
| :--- | :--- |
| **Document SHA-256 Digest** | Computed across the entire file bytes to detect if the file changed. |
| **Total Page Count (`page_count: 48`)** | Needed so chunks know their relative position (`Page 3 of 48`). |
| **Document Category & Year** | Extracted from file properties or document header (`year: 2021`). |
| **Source File Provenance** | Original path, safe slug, and extractor type (`docx_ast` vs `mistral_ocr`). |

---

### 7. Summary Engineering Formula

$$\text{KnowledgeChunk} = \underbrace{\text{Document Frontmatter}}_{\text{Inherited from Step 1 (Global)}} + \underbrace{\text{Section \& Page Coordinates}}_{\text{Calculated in Step 2 (Local)}} + \underbrace{\text{Text Slice}}_{\text{Extracted content}}$$

1. **Step 1 (Document Loading):** Extracts what is true about the **entire file** (from binary headers and file attributes) and saves it into the **YAML Frontmatter**.
2. **Step 2 (Chunking):** Slices the text, attaches the **local page/section coordinates**, and **inherits** the document frontmatter so the chunk has complete 360-degree context in the vector database.
