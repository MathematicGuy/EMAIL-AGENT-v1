# Retrieval Golden Set (SPEC-rag §5)

Labeled retrieval evaluation dataset for
[SPEC-rag-golden-set-and-eval.md](../../../tasks/specs/SPEC-rag-golden-set-and-eval.md).
Consumed by the retrieval evaluation harness (`scripts/evaluate_retrieval.py`)
and by the end-to-end email→corpus integration fixtures.

## Files

- `retrieval_golden.json` — the labeled cases (100 cases).
- `loader.py` — typed loader with schema **and corpus** validation, used by
  the harness and by the loader unit test.

## Case schema

Each case is an object with:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Unique, `q-NNN`. |
| `query` | string | yes | The retrieval query as the Route Classifier would emit it. Vietnamese, natural phrasing. |
| `probe` | enum | yes | `lexical` \| `semantic` \| `mixed` \| `unanswerable`. See probe types below. |
| `expected_document_ids` | string[] | yes | Corpus file stems under `data/extracted/`. **Empty iff** `probe == "unanswerable"`. Primary relevance key — stable across re-chunking. |
| `expected_sections` | string[] | yes | Section titles exactly as `load_corpus` emits them. May be empty when the answer spans a whole document. Secondary, chunking-sensitive key. |
| `email_body` | string \| null | yes | Non-null on cases doubling as end-to-end email fixtures. Synthetic. **Never real user data.** |
| `notes` | string | no | Why this case exists / which distractor it targets. Ignored by the harness. |

Labels are keyed on `document_id` + `section`, never on `chunk_id`:
`chunk_id` is positional (`f"{document_id}#{index}"`), so labelling on it
would silently break on every re-chunk.

## Probe types (SPEC §4)

The probe tag predicts *which retriever should win*, so the report can be
sliced. An aggregate that improves while `semantic` regresses is a failure,
and only the sliced report can see that.

| `probe` | Query construction | Expected behaviour |
|---|---|---|
| `lexical` | Contains an exact string only present in the corpus: a legal code, a portal URL, a form number | BM25 should win; dense embeddings blur numeric identifiers |
| `semantic` | Paraphrase with **near-zero token overlap** with the target chunk | Dense should win; BM25 should miss |
| `mixed` | Natural user phrasing — the realistic majority | Hybrid ≥ max(dense, BM25) |
| `unanswerable` | Plausible admin question the corpus genuinely cannot answer | `retrieval_status == no_results`, or nothing above `min_score` |

## Validation rules enforced by `loader.py`

All raise `RetrievalFixtureError` with a `path[index]:` prefix.

1. `id` unique and matching `^q-\d{3}$`.
2. `probe` is one of the four enum values.
3. `expected_document_ids` empty **iff** `probe == "unanswerable"`.
4. Every `expected_document_ids` entry is a real `data/extracted/*.md` stem.
5. Every `expected_sections` entry equals a `section` that `load_corpus`
   actually emits for one of that case's `expected_document_ids`.
6. Temporary fixtures retain generic per-document `lexical`, `semantic`, and
   `mixed` coverage plus an `unanswerable` case. The checked-in 100-case
   fixture instead enforces all 17 documents, exactly 12 unanswerables, and
   three-probe coverage for every newly covered document allocated at least
   three cases. `dang-ky-tam-tru` intentionally has two answerable cases.

Rules 4–6 need the corpus, so `load_retrieval_golden` takes an optional
`corpus_dir`. Pure-schema callers (rules 1–3) omit it; the harness always
passes it.

**Rule 5 is the anti-rot guard and the reason this loader exists at all.**
Without it, a re-chunk that renames a section makes the golden set silently
score 0.0 instead of failing loudly.

## Growing the set

The checked-in fixture preserves `q-001` through `q-032` unchanged. It then
allocates `q-033` through `q-072` to four large legal documents (10 each),
`q-073` through `q-090` to six detailed procedures (3 each), `q-091` through
`q-092` to `dang-ky-tam-tru`, and `q-093` through `q-100` to unanswerables.
All appended cases are normalized synthetic retrieval queries with
`email_body: null`; they are not email E2E fixtures.

Keep the SPEC §5.1 distribution satisfied when adding cases — one `lexical`,
one `semantic` and 2–3 `mixed` per document, plus the `unanswerable` block.
Authoring rules, in priority order:

1. `semantic` queries must share almost no content words with the target
   chunk. A paraphrase that still contains the official term is a wasted
   case: BM25 finds it and the case proves nothing.
2. `lexical` queries must contain a string appearing verbatim in exactly one
   chunk. Verify uniqueness against the corpus before committing the label.
3. Prefer targets with a near-duplicate sibling section. Document-level
   Hit@1 is trivially 1.0 across six disjoint documents, so only
   section-level MRR separates retrievers.
4. `expected_sections` must be copy-pasted from `load_corpus` output, not
   from the markdown source — rule 5 compares byte-for-byte.
5. Email bodies are synthetic, realistic in register, and never real user
   data.
