# Retrieval Golden Set V2 Design

## Goal

Create a versioned, retrieval-only golden set that represents the current
`data/extracted/` corpus of 17 Markdown documents. It will establish a
repeatable quality baseline before changes to chunking or query retrieval.

## Scope

- Add a new 100-case fixture beside the existing 32-case historical fixture.
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

The new fixture is `tests/fixtures/rag/retrieval_golden_v2.json`. Every case
retains the existing schema: `id`, `query`, `probe`,
`expected_document_ids`, `expected_sections`, `email_body`, and optional
`notes`. `email_body` is always `null` in V2.

The 100 cases are allocated as follows:

| Category | Cases | Contract |
| --- | ---: | --- |
| Answerable, per document | 85 | 5 cases for each of the 17 documents |
| Unanswerable | 15 | Empty expected-document and expected-section labels |
| Total | 100 | Exact cardinality |

Each document's five answerable cases contain one `lexical`, one `semantic`,
and three `mixed` probes. Lexical probes use an exact, unique body string;
semantic probes avoid distinctive target terms; mixed probes use natural,
normalized task wording. The four large legal documents must target distinct
chapters, articles, or sections rather than concentrating on their opening
material.

Labels remain stable across chunk-size changes by identifying an expected
document and the section emitted by `load_corpus`; they never use positional
chunk IDs. V2 will additionally document the source heading path in `notes`
until `heading_path` becomes a first-class runtime metadata field.

## Validation and evaluation

`loader.py` will gain an explicit V2 validation mode, selected by the V2
fixture, that verifies:

1. exactly 100 unique cases;
2. exactly five answerable cases per corpus document;
3. every document has the required 1 lexical / 1 semantic / 3 mixed split;
4. exactly 15 unanswerable cases; and
5. every expected section is emitted by the live corpus loader.

The harness will run the same fixture through the production-equivalent
Qdrant adapter and report document/section Hit@K, MRR, Recall@5, abstention
statistics, and latency. In-repo dense, BM25, and hybrid runs remain
diagnostic comparators, not production claims.

## Data flow

```text
retrieval_golden_v2.json
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
- The V2 fixture contains only synthetic queries and notes, complying with the
  project's raw-email non-persistence invariant.

## Tests

- Unit tests prove each V2 structural rule and the existing legacy fixture
  remains valid without the V2-only rules.
- A harness test proves V2 can be selected explicitly and its report carries
  the correct corpus and case counts.
- Qdrant adapter evaluation is covered with deterministic test doubles; a
  live production baseline requires configured Gemini embeddings and Qdrant.

## Acceptance criteria

- V2 contains exactly 100 valid cases across all 17 current corpus documents.
- Fixture validation rejects any coverage or label drift.
- Existing 32-case fixture and its tests keep passing unchanged.
- A documented command produces a Qdrant V2 baseline report without treating
  service failures as `no_results` quality evidence.
