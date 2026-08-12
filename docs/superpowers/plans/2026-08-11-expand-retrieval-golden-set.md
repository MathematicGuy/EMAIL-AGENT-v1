# Expand Retrieval Golden Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `retrieval_golden.json` from 32 to 100 normalized retrieval queries over all 17 corpus documents and benchmark the production-equivalent Qdrant adapter.

**Architecture:** Keep the one fixture and append `q-033` through `q-100`, leaving the first 32 JSON objects untouched. The loader enforces the 100-case contract only when loading the checked-in fixture, so temporary schema fixtures remain small. An in-memory Qdrant wrapper uses `ingest_corpus` and `QdrantSemanticMemory` for the actual production adapter path.

**Tech Stack:** Python, pytest, JSON, Gemini embeddings, Qdrant.

## Global Constraints

- Every appended case has `email_body: null`; raw emails remain out of fixtures and reports.
- Preserve `q-001` through `q-032` byte-for-byte.
- Append 40 large-law answerable cases, 18 detailed-procedure answerable cases, 2 temporary-residence answerable cases, and 8 unanswerable cases.
- Labels use document IDs and live `load_corpus` section titles, never positional chunk IDs.
- Do not change runtime chunking, ranking, rewriting, or thresholds.
- Do not stage `data/extracted/ingestion-manifest.json`.

---

### Task 1: Validate the expanded checked-in fixture

**Files:**
- Modify: `tests/fixtures/rag/loader.py`
- Modify: `tests/unit/fixtures/test_retrieval_golden.py`
- Modify: `tests/unit/integrations/rag/test_rag.py`

**Interfaces:**
- Produces `_validate_repository_fixture_contract(cases: Sequence[RetrievalCase], source: Path) -> None`.

- [ ] **Step 1: Write failing tests**

```python
def test_repository_fixture_requires_one_hundred_cases() -> None:
    with pytest.raises(loader.RetrievalFixtureError, match="exactly 100"):
        loader._validate_repository_fixture_contract((), FIXTURE_PATH)

def test_load_corpus_reads_seventeen_committed_documents() -> None:
    assert len(load_corpus(CORPUS_DIR, tenant_id="local")) == 17
```

- [ ] **Step 2: Verify red**

Run `python -m pytest tests/unit/fixtures/test_retrieval_golden.py::test_repository_fixture_requires_one_hundred_cases tests/unit/integrations/rag/test_rag.py::test_load_corpus_reads_seventeen_committed_documents -q`. Expect missing helper and legacy six-document assertion failures.

- [ ] **Step 3: Implement the contract**

```python
EXPANDED_CASE_COUNT = 100
LEGACY_CASE_IDS = tuple(f"q-{number:03d}" for number in range(1, 33))

def _validate_repository_fixture_contract(cases: Sequence[RetrievalCase], source: Path) -> None:
    if len(cases) != EXPANDED_CASE_COUNT:
        raise RetrievalFixtureError(f"{source}: expected exactly 100 cases")
    if tuple(case.id for case in cases[:32]) != LEGACY_CASE_IDS:
        raise RetrievalFixtureError(f"{source}: legacy case IDs q-001 through q-032 must lead")
```

Call it only when `source.resolve() == DEFAULT_FIXTURE_PATH.resolve()` and `corpus_dir` is present. Add rules for 17-document coverage, lexical/semantic/mixed coverage of the 11 newly covered documents, and exactly 12 unanswerable cases.

- [ ] **Step 4: Verify green after Task 2**

Run `python -m pytest tests/unit/fixtures/test_retrieval_golden.py tests/unit/integrations/rag/test_rag.py -q`. Expect PASS.

- [ ] **Step 5: Commit**

Commit the loader, tests, and fixture changes as `test: expand retrieval golden set to 100 cases`.

### Task 2: Append the 68 synthetic queries

**Files:**
- Modify: `tests/fixtures/rag/retrieval_golden.json`
- Modify: `tests/fixtures/rag/README.md`

**Interfaces:**
- Produces a valid `load_retrieval_golden(corpus_dir=CORPUS_DIR)` 100-case dataset.

- [ ] **Step 1: Write a failing dataset test**

```python
def test_real_fixture_has_expanded_distribution() -> None:
    cases = loader.load_retrieval_golden(corpus_dir=CORPUS_DIR)
    assert len(cases) == 100
    assert sum(case.probe is loader.Probe.UNANSWERABLE for case in cases) == 12
    assert all(case.email_body is None for case in cases[32:])
```

- [ ] **Step 2: Verify red**

Run `python -m pytest tests/unit/fixtures/test_retrieval_golden.py::test_real_fixture_has_expanded_distribution -q`. Expect FAIL with 32 cases.

- [ ] **Step 3: Author additions without changing legacy objects**

```text
q-033..q-072: 10 answerable cases for each of the four large legal documents
q-073..q-090: 3 answerable cases for each of the six chi-tiet-thu-tuc documents
q-091..q-092: 2 answerable cases for dang-ky-tam-tru
q-093..q-100: 8 unanswerable cases
```

Every new document gets lexical, semantic, and mixed probes. Copy `expected_sections` from `load_corpus`; add source-heading and distractor rationale to `notes`. Large-law cases must cover distinct chapters/articles/sections.

- [ ] **Step 4: Document and verify**

Update the README with the allocation, immutable legacy rule, and query-only rule. Run `python -m pytest tests/unit/fixtures/test_retrieval_golden.py -q`. Expect PASS.

### Task 3: Keep email E2E independent

**Files:**
- Modify: `tests/integration/email_action_plan/test_rag_retrieval_golden.py`

**Interfaces:**
- Consumes `RetrievalCase.email_body`; produces legacy-email-only E2E parameters.

- [ ] **Step 1: Write failing boundary test**

```python
def test_email_e2e_cases_are_legacy_cases_only() -> None:
    cases = tuple(case for case in load_retrieval_golden(corpus_dir=CORPUS_DIR) if case.email_body)
    assert cases and all(int(case.id.removeprefix("q-")) <= 32 for case in cases)
```

- [ ] **Step 2: Verify red**

Run `python -m pytest tests/integration/email_action_plan/test_rag_retrieval_golden.py::test_email_e2e_cases_are_legacy_cases_only -q`. Expect failure from its all-corpus email-coverage guard.

- [ ] **Step 3: Implement fixed legacy scope and verify green**

```python
LEGACY_EMAIL_DOCUMENT_IDS = frozenset({
    "cap_lai_cccd", "dang_ky_ket_hon", "dang_ky_xe",
    "huong_dan_nop_ho_so_dai_hoc_vinuni", "thue_dien_tu",
    "thu_tuc_dang_ky_bhxh_luatvietnam",
})
```

Use this set for the coverage assertion. Run `python -m pytest tests/integration/email_action_plan/test_rag_retrieval_golden.py -q`. Expect PASS. Commit as `test: separate retrieval and email rag fixtures`.

### Task 4: Benchmark the Qdrant adapter

**Files:**
- Modify: `scripts/evaluate_retrieval.py`
- Modify: `tests/unit/scripts/test_evaluate_retrieval.py`

**Interfaces:**
- Produces `QdrantEvaluationRetriever` implementing `build_index()` and `retrieve(request)`.

- [ ] **Step 1: Write failing construction test**

```python
def test_build_retriever_constructs_qdrant(documents: tuple[KnowledgeDocument, ...]) -> None:
    retriever = evaluate_retrieval.build_retriever("qdrant", documents, HashingEmbedder(), top_k=5, min_score=0.2)
    assert isinstance(retriever, evaluate_retrieval.QdrantEvaluationRetriever)
```

- [ ] **Step 2: Verify red**

Run `python -m pytest tests/unit/scripts/test_evaluate_retrieval.py::test_build_retriever_constructs_qdrant -q`. Expect FAIL because `qdrant` is unsupported.

- [ ] **Step 3: Implement production-equivalent wrapper**

```python
class QdrantEvaluationRetriever:
    async def build_index(self) -> None:
        await ingest_corpus(self._client, "retrieval-eval", self._documents, self._embedder)
        self._memory = QdrantSemanticMemory(self._client, "retrieval-eval", self._embedder, top_k_default=self._top_k, min_score_default=self._min_score)

    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        if self._memory is None:
            raise RuntimeError("build_index() must be called before retrieve()")
        return await self._memory.retrieve(request)
```

Construct the client as `AsyncQdrantClient(":memory:")`; add `qdrant` to CLI choices and classify it as dense-cosine score evidence.

- [ ] **Step 4: Verify green and commit**

Run `python -m pytest tests/unit/scripts/test_evaluate_retrieval.py tests/integration/test_qdrant_integration.py -q`. Expect PASS. Commit as `feat: benchmark qdrant retrieval adapter`.

### Task 5: Record the first baseline

**Files:**
- Create: `docs/baselines/retrieval-eval-YYYY-MM-DD-gemini-qdrant.json`
- Modify: `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md`

- [ ] **Step 1: Validate locally**

Run `python scripts/evaluate_retrieval.py --dry-run --retriever qdrant --output docs/baselines/retrieval-eval-YYYY-MM-DD-hashing-qdrant.json`. Expect a report with 100 cases, 17 documents, and `retriever: qdrant`.

- [ ] **Step 2: Run the live baseline**

Run `python scripts/evaluate_retrieval.py --embedder gemini --retriever qdrant --output docs/baselines/retrieval-eval-YYYY-MM-DD-gemini-qdrant.json`. If credentials are unavailable, stop and report the configuration blocker; do not update status.

- [ ] **Step 3: Record observed results and verify**

Record the command, report name, corpus/case counts, per-probe metrics, abstention, and latency; do not compare directly against the old 6-document benchmark. Run focused fixture/harness/E2E/Qdrant tests, then `python -m ruff check .` and `python -m mypy src`. Expect all PASS.

- [ ] **Step 4: Commit**

Commit the baseline JSON and status update as `docs: record current corpus qdrant baseline`.
