# Expanded retrieval golden set: task report

## Result

Completed in commit `b490963` (`test: expand retrieval golden set to 100 cases`).
The checked-in retrieval fixture now contains 100 cases. It preserves the
first 32 fixture objects, appends 68 query-only cases, covers all 17 corpus
documents, and contains exactly 12 unanswerable cases. The special allocation
for `dang-ky-tam-tru` remains two answerable cases; three-probe coverage is
therefore required only for the other ten newly covered documents.

## Files changed

- `tests/fixtures/rag/retrieval_golden.json` — appended `q-033` through
  `q-100` without changing the legacy objects.
- `tests/fixtures/rag/loader.py` — added the repository-only 100-case,
  immutable-leading-ID, full-corpus, new-document probe, and unanswerable
  contract. Temporary fixtures retain the generic contract.
- `tests/fixtures/rag/README.md` — documented the allocation, immutable
  legacy prefix, and query-only appended cases.
- `tests/unit/fixtures/test_retrieval_golden.py` — added repository-contract
  and expanded-distribution coverage.
- `tests/unit/integrations/rag/test_rag.py` — updated the committed corpus
  inventory assertion to all 17 current documents.

`data/extracted/ingestion-manifest.json` was not modified or staged by this
task. Its working-tree change is unrelated and remains uncommitted.

## Test evidence

Red state, before the 68 fixture entries were present:

```text
py -3.11 -m pytest tests/unit/fixtures/test_retrieval_golden.py::test_repository_fixture_requires_one_hundred_cases tests/unit/fixtures/test_retrieval_golden.py::test_real_fixture_has_expanded_distribution tests/unit/integrations/rag/test_rag.py::test_load_corpus_reads_the_committed_documents -q
1 failed, 2 passed
```

Expected failure: the checked-in fixture had 32 rather than 100 cases.

Green verification:

```text
py -3.11 -m pytest tests/unit/fixtures/test_retrieval_golden.py tests/unit/integrations/rag/test_rag.py -q
30 passed, 5 warnings
```

The five warnings are existing `InRepoSemanticMemory` deprecation warnings in
the RAG unit tests, unrelated to the fixture work.

## Self-review

- Confirmed exact fixture count, IDs `q-001` through `q-100`, document
  allocation (four legal documents × 10, six detailed procedures × 3,
  `dang-ky-tam-tru` × 2), and 12 unanswerables.
- Confirmed every appended case has `email_body: null`.
- Confirmed each expected section is emitted by `load_corpus`; law-document
  cases correctly use empty section labels because their corpus chunks have no
  emitted Markdown sections.
- Confirmed no change to the ingestion manifest was included in commit
  `b490963`.
