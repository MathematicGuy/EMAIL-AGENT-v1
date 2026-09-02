# Structure-Aware Chunking — Implementation Record

> **Status: implemented and measured on 2026-08-17.** This is a record of work already
> landed, not a task list. Gates run: `pytest tests/unit` (1268 passed), `ruff check`,
> `scripts/evaluate_retrieval.py` before/after.

**Trigger:** the question *"điều 1 của THỎA THUẬN & CAM KẾT … là gì"* returned nothing usable.
The cause was not the retriever.

---

## 1. Diagnosis

Two distinct chunking defects, each hitting a different half of the corpus, plus the extraction
bug underneath the second one. Both were reproduced by running the pre-change chunker from
`HEAD` — the outputs below are real, not reconstructed.

**Defect A — the heading line was dropped from the chunk text.** This fires only when a line
*is* recognised as a heading, so it hit the 13 OCR-derived documents that carry real ATX
headings. The heading reached the `section` field but was never appended to the chunk body:
the chunk labelled `Lệ phí đăng ký kết hôn` contains no occurrence of that string in `text`.
Neither dense embedding nor BM25 — which indexes `chunk.text` — had anything to match.

**Defect B — no line was recognised as a heading at all.** This is what hit the four statutes.
`chunk_markdown_pages()` understood only `^#{1,2}`, and their Markdown carried no heading of
any kind, so everything became body text packed blindly to a 1200-character limit. Chunk #0
(1118 chars, `section=None`) runs the masthead table, `LUẬT`, `ĐẤT ĐAI`, the preamble,
`Chương I`, `QUY ĐỊNH CHUNG` and the opening of Điều 1 together, then cuts mid-word. Chunk #1
begins at `1. Bản đồ địa chính…` with nothing to say which article it belongs to — that pattern
is the 50% figure in the table below. Note the contrast with Defect A: here `Điều 1. Phạm vi
điều chỉnh` *is* present in the text, because nothing was recognised and therefore nothing was
stripped; what is missing is any boundary, label, or anchor.

**Extraction — the root cause of Defect B.** The four statutes in the corpus are `.docx`, and
`ingestion-manifest.json` records `extractor: "docx"`: our own `DocxExtractor`, not Mistral OCR.
Reading the sources with python-docx:

| Source | Paragraphs with a Word Heading style | `Điều N` lines in bold |
|---|---|---|
| `31_2024_QH15_523642.docx` | 0 of 2861 (all `Normal`) | 260 / 260 |
| `01_2021_ND-CP_283247.docx` | 0 of 792 | 101 / 101 |
| `49_2019_QH14_402073.docx` | 0 of 424 | 52 / 52 |

`_block_to_markdown` consulted only `block.style.name` and discarded all run-level formatting.
Bold was the sole structural signal these documents carried, and extraction is the last stage
that can see it. The emitted Markdown had **0 bold markers and 0 ATX headings**, while
OCR-derived documents had 4–13 headings each.

**Measured consequences** (`data/extracted`, 17 documents):

| | Before |
|---|---|
| `section=None` | 937 / 1069 (88%); the four statutes were 931 / 931 = 100% |
| Statute chunks with no structural anchor in their own text | 463 / 931 (50%) |
| Documents emitting no section at all | 4 / 17 |
| Golden cases dropped from the gating section-level metric | 40 |

The harness had already encoded the defect without naming it:
`test_evaluate_retrieval.py` asserted `excluded_case_count == 12` — four section-less
documents × three probes.

---

## 2. Design — three stages, one shared profile

```
extract (keep the signal) → normalize (recover it from text) → chunk (hierarchical)
```

All three consult one [`StructureProfile`](../../../src/cowork_agent/integrations/rag/structure_profile.py),
so they cannot disagree about what a heading is or how deep it sits.

**A. Extraction** — [`docx_extractor.py`](../../../src/cowork_agent/integrations/knowledge_ingestion/docx_extractor.py).
A paragraph whose every text-bearing run is bold, and which is short enough, becomes a heading.
Requiring *every* run to be bold is what separates a heading from a sentence containing a bold
phrase. Word Heading styles and list styles still win first. Only `DocxExtractor` was touched;
`pdf_inspector` and Mistral OCR are third-party.

**B. Normalization** — [`structure_normalizer.py`](../../../src/cowork_agent/integrations/rag/structure_normalizer.py)
(new). Promotes standalone plain-text headings to ATX. Needed because stage A cannot cover the
PDF/OCR path, and because uploaded project documents are never persisted as Markdown at all
([c1-system-context.md](../../architectures/c1-system-context.md) forbids storing their
extracted text) — the text itself is the only surviving evidence of their structure. Idempotent,
and called in-memory from both `load_corpus()` and `ProjectDocumentExtractor.extract()`.

A bare division adopts the uppercase title beneath it, so `Chương I` + `QUY ĐỊNH CHUNG` becomes
one heading instead of a breadcrumb reading `Chương I`.

**C. Chunking** — [`markdown_chunking.py`](../../../src/cowork_agent/integrations/rag/markdown_chunking.py).

- Typed block parser (`heading` / `table` / `code` / `list` / `boilerplate` / `paragraph`), so
  "never split a table, fence, or list item" is a property of the parser. Page-marker comments
  are classified as boilerplate and dropped.
- Hierarchical emission: always descend to the leaf, then merge back up. Collapsing a subtree
  that happens to fit would label a chunk with its chapter when the article is the answer.
- Every chunk repeats its heading breadcrumb in its own text, budgeted out of `max_chars`.
- Overlap of ~180 characters between consecutive cuts **within one leaf only** — never across an
  article boundary, and never for tables or fenced code (carrying a closing fence forward
  strands an unmatched one).
- Sizing: `target 1200 / max 2000 / min 300 / overlap 180`. Structure decides boundaries; size
  is only the constraint applied where structure runs out.

`MarkdownChunk` gained `heading_path`; `text`, `section`, `page_start`, `page_end` are unchanged.

---

## 3. Results

Same corpus, same harness, baseline measured in a detached `HEAD` worktree.

| | Before | After |
|---|---|---|
| `section=None` | 937 / 1069 (88%) | **10 / 939 (1%)** |
| Statute chunks with no structural anchor | 463 / 931 (50%) | **11 / 822 (1%)** |
| Documents emitting no section | 4 / 17 | **0 / 17** |
| Shortest chunk | 6 chars | **33 chars** |
| Golden cases excluded from section metric | 40 | **2** |

`scripts/evaluate_retrieval.py --embedder hashing --retriever hybrid` — no slice regressed:

| | doc hit@1 | doc hit@3 | doc mrr | doc recall@5 | sec hit@1 | sec mrr | sec recall@5 |
|---|---|---|---|---|---|---|---|
| before | 0.6818 | 0.7955 | 0.7409 | 0.8295 | 0.3261 | 0.4362 | 0.6087 |
| after | **0.7045** | **0.8636** | **0.7862** | **0.9091** | **0.3478** | **0.4467** | **0.6304** |

Baseline: [`retrieval-eval-2026-08-17-hashing-hybrid.json`](../../../evaluations/baselines/retrieval-eval-2026-08-17-hashing-hybrid.json).
Its section-level figures are measured over 86 labelled cases rather than 46, so they are not
directly comparable to the row above — that is the point of the labelling work.

---

## 4. Decisions worth knowing

**No rule for bare numbering (`1. Title`).** Measured over `data/extracted` such a rule fired 8
times and every hit was an enumerated clause inside an article. Because its guard is
length-based it promoted only the *short* items — splitting Điều 27 at khoản 1 and leaving
khoản 2–4 in the body. Genuine outline headings arrive as ATX from extraction anyway.

**Stub chunks merge only within one section, never up into the parent.** The stated target of
"zero chunks under 300 characters" was not met and was the wrong target: small chunks rose
39 → 58, but the shortest went 6 → 33 and each now carries its breadcrumb.
`CƠ QUAN THỰC HIỆN / Công an cấp Xã` answers a real question; a bare `Công an cấp Xã` does not.
Merging upward would cost the section label the gating metric measures.

**`heading_path` was not threaded into `KnowledgeChunk` / `ProjectDocumentChunk`.** No consumer
exists; it would be a dead field on a persisted contract. It is available on `MarkdownChunk`
when something needs it.

**`LEGACY_CASE_SNAPSHOT_SHA256` was rotated once, deliberately.** Case q-016 expected the
section `2. Chuẩn bị giấy tờ cần thiết trong bộ hồ sơ`, a label that existed only while the
chunker ignored H3. That heading has no body of its own, so the answer now sits under its table
sub-heading. The fixture's own rule-5 validator is what caught it. Both copies of the constant
(`tests/fixtures/rag/loader.py`, `tests/unit/fixtures/test_retrieval_golden.py`) carry a note.

**Golden set.** 40 statute cases were labelled from the article numbers already recorded in each
case's own `notes` field, cross-checked against the sections `load_corpus()` emits; q-051 named
only `Chương VII` and resolved to Điều 91 (`Nguyên tắc bồi thường…`), verified by breadcrumb.

---

## 5. Open items

- **q-019 and q-021** (`dang-ky-xe`) remain unlabelled, holding `excluded_case_count` at 2
  instead of 0. Untouched because they sit inside the locked 32-case block and nothing in this
  change forced an edit. Note q-019's own note is now **stale** — it records that
  `Quyết định 1383/QĐ-BCA` "appears only in the H1 heading, and `_split_sections` excludes
  heading lines from chunk text". Heading lines are in chunk text now.
- **Project documents in Qdrant need re-uploading.** The breadcrumb is inside the embedded text,
  so the 23 existing points in `project_documents` are not comparable with newly written
  vectors. Company knowledge rebuilds at startup and needs no action.
- **Re-extraction is mode-sensitive.** `.env` sets `EXTRACTION_MODE=advance`, which routes
  `.docx` through Mistral OCR. The four statutes were re-extracted with `EXTRACTION_MODE=basic`
  over a source directory holding only those four files, so the 13 OCR-derived Markdown files
  were left untouched.
- Pre-existing and unrelated: `tests/unit/integrations/test_reranker.py` and
  `test_key_rotation.py` fail collection with `'asyncio' not found in markers configuration
  option`.
