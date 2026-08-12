# Retrieval Golden Set V2 Design

## Goal

Expand the existing retrieval-only golden set to 100 cases representing the
current `data/extracted/` corpus of 17 Markdown documents. It will establish a
repeatable quality baseline before changes to chunking or query retrieval,
while preserving the historical 32-case baseline.

## Scope

- Keep every existing case unchanged and append 68 new cases to the same
  fixture.
- Evaluate normalized `retrieval_query` text only; it must not contain raw
  email subjects or bodies.
- Cover all current documents and include unanswerable questions.
- Reuse the existing evaluation harness where possible, but make its fixture
  validation enforce the V2 coverage contract.
- Add a Qdrant-compatible evaluation path so the production retrieval adapter
  can be measured separately from the deprecated in-repo fallback.

Out of scope:

- Changing production chunking, query rewriting, ranking, or threshold policy.
- Measuring the email classifier's conversion of email content to a retrieval
  query. That is a separate E2E evaluation set.
- Persisting email content in any fixture or report.

## Fixture design

The expanded fixture remains `tests/fixtures/rag/retrieval_golden.json`. Every
case retains the existing schema: `id`, `query`, `probe`,
`expected_document_ids`, `expected_sections`, `email_body`, and optional
`notes`. The 68 appended cases have `email_body: null`.

The 100 cases are allocated by document breadth, not by a uniform per-document
quota:

| Category | Cases | Contract |
| --- | ---: | --- |
| Preserved historical cases | 32 | Existing cases are byte-for-byte unchanged |
| Four large legal documents | 40 | 10 answerable cases per document, across distinct chapters/articles/sections |
| Six detailed procedure documents | 18 | 3 answerable cases per document |
| `dang-ky-tam-tru.md` | 2 | 2 answerable cases, proportionate to its short content |
| New unanswerable cases | 8 | Empty expected-document and expected-section labels |
| Total | 100 | Exact cardinality |

The preserved 32 cases already cover the six short/medium guide documents.
For every newly covered document, answerable cases include at least one
`lexical`, one `semantic`, and one `mixed` probe. Lexical probes use an exact,
unique body string; semantic probes avoid distinctive target terms; mixed
probes use natural, normalized task wording. The four large legal documents
must target distinct chapters, articles, or sections rather than concentrating
on their opening material.

Labels remain stable across chunk-size changes by identifying an expected
document and the section emitted by `load_corpus`; they never use positional
chunk IDs. V2 will additionally document the source heading path in `notes`
until `heading_path` becomes a first-class runtime metadata field.

## Validation and evaluation

`loader.py` will validate the expanded fixture and verify:

1. exactly 100 unique cases;
2. the original `q-001` through `q-032` cases are retained unchanged;
3. all 17 corpus documents have answerable coverage;
4. each document newly covered by cases `q-033` through `q-092` has at least
   one lexical, one semantic, and one mixed probe;
5. exactly 12 unanswerable cases (the four existing plus eight new); and
6. every expected section is emitted by the live corpus loader.

The harness will run the same fixture through the production-equivalent
Qdrant adapter and report document/section Hit@K, MRR, Recall@5, abstention
statistics, and latency. In-repo dense, BM25, and hybrid runs remain
diagnostic comparators, not production claims.

## Data flow

```text
retrieval_golden.json
  -> validated against data/extracted/ and load_corpus output
  -> build selected retriever
  -> retrieve normalized query
  -> compare returned document + section to labels
  -> baseline report with corpus and fixture provenance
```

## Error handling and quality controls

- A deleted/renamed corpus document or section fails fixture validation before
  metrics are calculated.
- A duplicate query ID or invalid probe distribution fails validation.
- Qdrant unavailability is reported as a retriever setup/runtime failure, not
  silently interpreted as relevant retrieval quality.
- The expanded fixture contains only synthetic queries and notes, complying with the
  project's raw-email non-persistence invariant.

## Tests

- Unit tests prove each expanded-fixture structural rule and retain coverage
  for the existing fixture behavior.
- A harness test proves the expanded fixture produces the correct corpus and
  case counts.
- Qdrant adapter evaluation is covered with deterministic test doubles; a
  live production baseline requires configured Gemini embeddings and Qdrant.

## Acceptance criteria

- The expanded fixture contains exactly 100 valid cases across all 17 current
  corpus documents.
- Fixture validation rejects any coverage or label drift.
- Existing 32-case fixture and its tests keep passing unchanged.
- A documented command produces a Qdrant V2 baseline report without treating
  service failures as `no_results` quality evidence.
