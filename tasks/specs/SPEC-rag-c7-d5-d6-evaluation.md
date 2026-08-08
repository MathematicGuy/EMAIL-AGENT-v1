# SPEC — Remaining Email-RAG Evaluation Gaps (C7, D5, D6)

> **Status:** Approved for implementation
> **Created:** 2026-08-09
> **Approved scope:** Evaluation-only C7 and D6 plus the dependency-free,
> human-labelled D5 smoke/aggregation contract. Runtime abstention and automated
> semantic judging remain separate ask-first work.
> **Depends on:** `tasks/specs/SPEC-rag-golden-set-and-eval.md` and commit `35fa54a`
> **Authority:** This document narrows the open items recorded in
> `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md`. It does not authorize a
> production abstention policy, a live judge, or a new dependency.

## 1. Objective

Add honest, reproducible evidence for the three remaining evaluation gaps:

1. **C7 — abstention calibration:** retain enough score evidence to measure
   unanswerable abstention against answerable false-abstention before changing
   runtime behavior.
2. **D6 — deterministic context relevance:** report binary section Precision@5
   beside the existing section Recall@5 using the current golden labels.
3. **D5 — faithfulness decision:** define the smallest dependency-free,
   human-labelled scoring-contract smoke test, while keeping automated semantic
   faithfulness open unless a judge or semantic model is explicitly authorized.

Success means the report distinguishes measured evidence from policy or semantic
claims. It must never imply that lexical overlap, target-section labels, or a
human-labelled fixture automatically proves arbitrary generated prose is grounded.

## 2. Tech stack

- Python 3.11+ and pytest.
- Existing stdlib + project dependencies only for the default scope.
- Existing golden set: `tests/fixtures/rag/retrieval_golden.json`.
- Existing retrieval harness: `scripts/evaluate_retrieval.py`.
- No RAGAS, NLI model, LLM judge, new provider call, or new credential in the
  default scope.

## 3. Commands

Focused tests:

```powershell
python -m pytest tests/unit/scripts/test_evaluate_retrieval.py -q
python -m pytest tests/unit/fixtures/test_retrieval_golden.py -q
python -m pytest tests/unit/scripts/test_evaluate_faithfulness.py tests/unit/fixtures/test_faithfulness_golden.py -q
```

Quality checks when the corresponding files exist or change:

```powershell
python -m ruff check scripts/evaluate_retrieval.py scripts/evaluate_faithfulness.py tests/fixtures/rag/faithfulness_loader.py tests/unit/scripts/test_evaluate_retrieval.py tests/unit/scripts/test_evaluate_faithfulness.py tests/unit/fixtures/test_faithfulness_golden.py
python -m mypy scripts/evaluate_retrieval.py scripts/evaluate_faithfulness.py tests/fixtures/rag/loader.py tests/fixtures/rag/faithfulness_loader.py
```

Offline evidence:

```powershell
$retrievalReport = Join-Path ([System.IO.Path]::GetTempPath()) ("email-agent-rag-spec-{0}.json" -f [guid]::NewGuid())
python scripts/evaluate_retrieval.py --dry-run --output $retrievalReport
```

Live Gemini/Jina calibration is a separate, explicitly approved operation using
the existing evaluator options. A dry run proves harness mechanics only; it does
not select a production threshold.

## 4. Project structure

```text
scripts/evaluate_retrieval.py
    C7 score evidence and calibration sweeps; D6 Precision@5
tests/unit/scripts/test_evaluate_retrieval.py
    Pure metric, privacy, report-shape, and sweep tests
tests/fixtures/rag/retrieval_golden.json
tests/fixtures/rag/loader.py
    Existing D6 binary document/section authority
scripts/evaluate_faithfulness.py
    D5 human-labelled verdict inventory and aggregation only
tests/fixtures/rag/faithfulness_golden.json
    Synthetic claim/evidence/verdict smoke fixture
tests/fixtures/rag/faithfulness_loader.py
    Strict D5 fixture loader
tests/unit/scripts/test_evaluate_faithfulness.py
    D5 inventory and aggregation tests
tests/unit/fixtures/test_faithfulness_golden.py
    D5 fixture-loader tests
docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md
    Evidence-backed status reconciliation after verification
```

## 5. Code style and report contracts

Metric functions stay pure and explicit about empty denominators:

```python
def precision_at_k(
    ranked: Sequence[tuple[str, str | None]],
    expected_documents: frozenset[str],
    expected_sections: frozenset[str],
    *,
    k: int,
) -> float:
    considered = ranked[:k]
    if not considered:
        return 0.0
    relevant = sum(
        document_id in expected_documents and section in expected_sections
        for document_id, section in considered
    )
    return relevant / len(considered)
```

Reports contain identifiers, enums, counts, booleans, and numeric scores only.
Tests enforce recursive closed key/type schemas, with forbidden-field checks as
defense in depth. Query text, chunk text, email evidence, prompts, model responses,
and raw email bodies are not written to reports or logs.

## 6. Detailed behavior

### 6.1 C7 — evaluation-only abstention calibration

Extend per-case evaluation evidence with privacy-safe score summaries:

- configured score kind and observed score kind (`dense_cosine`, `bm25`, `rrf`,
  or `jina`); observed kind is null when no score was returned;
- whether reranking was requested and whether a rerank score was actually present;
- top score, runner-up score, and their delta;
- no query or chunk content.

The per-case report schema is a closed `score_evidence.cases` array containing
only case/probe identifiers, configured/observed kinds, reranker flags,
rank-ordered scores, top score, runner-up score, and delta. Configured provenance
is threaded into empty cases; no observed score kind is invented for them.

Generate separate absolute-score and margin sweeps per score kind. Each candidate
must report:

- unanswerable abstention count/rate over `q-029`–`q-032`;
- answerable false-abstention count/rate over the 28 answerable cases;
- affected answerable case IDs;
- existing document/section metrics per probe after the simulated gate.

Do not combine dense, BM25, RRF, and Jina values under one numeric threshold. Do
not change `HybridSemanticMemory`, `InRepoSemanticMemory`, retrieval contracts, or
`DigestWorker` in this phase. A runtime policy is a later decision based on fresh
Gemini/Jina evidence.

### 6.2 D6 — binary section context relevance

Add macro binary section Precision@5 using the same relevance rule as the existing
section rank/recall metrics:

- relevant iff both `document_id` and `section` match the case labels;
- denominator is the number of returned chunks considered up to five;
- no returned chunks scores `0.0`;
- unanswerable and empty-section cases are excluded from answerable section
  precision and counted as `excluded_unanswerable_case_count` and
  `excluded_empty_expected_sections_case_count`;
- report overall and per probe beside existing section Recall@5.

Name the metric `binary_section_precision_at_5` (or equally explicit wording), not
`RAGAS Context Precision`. Current labels identify intended target sections but are
not exhaustive relevance judgements. Multiple chunks under one labelled section
are all treated as relevant by this v1 metric.

### 6.3 D5 — dependency-free labelled smoke contract

Use only synthetic evidence and human-authored atomic claim verdicts. The fixture
records:

- case and claim IDs;
- plan step number and claim text;
- allowed evidence origins (`email` and/or chunk IDs);
- expected `supported` or `unsupported` verdict;
- no real user content.

The pure harness validates fixture shape and inventories/aggregates labelled
verdicts:

- `supported_claims / total_claims`;
- unsupported claim IDs and step numbers;
- explicit zero-claim behavior;
- evidence-origin breakdown.

The fixture must include a low-overlap supported paraphrase, a fabricated
approver/threshold/deadline/document name, an email-supported uncited claim, an
unsupported uncited company claim, and a multi-citation claim.

This proves fixture-label inventory, claim accounting, and evidence attribution
only. Its supported ratio describes fixture composition, not generated-plan
quality. It does not infer faithfulness for unseen prose. The status report must
label it a human-labelled smoke/aggregation contract, leave D5 generation
faithfulness open/partial, and not count it as automated coverage.

## 7. Testing strategy

### C7

- Pure sweep tests with hand-built score distributions.
- Boundaries: score equal to threshold, one-result cases, missing runner-up,
  zero-result cases, and mixed score kinds.
- Report privacy tests enforce recursive closed key/type schemas and also forbid
  known text-bearing fields.
- Regression test ensures existing ranking metrics are unchanged when no
  simulated gate is applied.

### D6

- Relevant at ranks 1 and 5; irrelevant at rank 6.
- Partial precision, zero results, duplicate chunks under one relevant section,
  wrong section in the correct document, and correct section in the wrong document.
- Macro aggregation and per-probe reporting.

### D5

- Fixture schema and uniqueness validation.
- Supported/unsupported aggregation, evidence-origin accounting, zero claims, and
  deterministic ordering of unsupported IDs.
- Tests describe this as a labelled contract, never an automatic semantic judge.

## 8. Boundaries

### Always

- Preserve unrelated dirty work and stage explicit paths only.
- Use synthetic evidence; keep reports metadata/numbers-only.
- Preserve current ranking, ACL, provider fallback, and workflow behavior in the
  evaluation-only phase.
- Report dry-run evidence separately from live Gemini/Jina evidence.

### Ask first

- Any production abstention gate or default threshold/margin.
- Any change to `SemanticRetrievalResponse`, `DigestWorker`, or retrieval runtime.
- RAGAS, an LLM/NLI judge, a new dependency, network use, credentials, or paid API
  calls.
- Calling a human-labelled D5 smoke test “automated faithfulness coverage.”

### Never

- Persist or log raw email bodies or attachment content.
- Use one global threshold across incompatible score kinds.
- Tune against only the four unanswerable cases.
- weaken or relabel the golden set to make a gate pass.

## 9. Success criteria

### Evaluation-only phase

- [ ] C7 reports score kind, top/runner-up/delta, and per-score-kind calibration
      sweeps with both abstention and false-abstention effects.
- [ ] C7 makes no runtime behavior or shared-contract change.
- [ ] D6 reports binary section Precision@5 overall and per probe, beside Recall@5.
- [ ] Existing Hit@K, MRR, Recall@5, abstention, and privacy contracts remain
      unchanged.
- [ ] Focused tests, Ruff, and mypy pass for every changed file.
- [ ] Status documentation distinguishes available evaluation evidence from open
      runtime or semantic automation work.

### Later runtime decision

- [ ] Fresh Gemini dense/hybrid and actual Jina evidence exists with score summaries.
- [ ] A proposed policy states answerable false-abstention and per-probe regression
      limits before production code changes.
- [ ] RRF or Jina fallback behavior is not silently interpreted in another score
      space.

## 10. Recorded implementation decision

The approved implementation tranche is:

1. evaluation-only C7 score evidence and calibration sweeps;
2. deterministic D6 binary section Precision@5; and
3. the dependency-free, human-labelled D5 smoke/aggregation contract.

The D5 contract is partial/manual evidence, not an automated semantic judge. A
runtime abstention policy remains a later, separately specified decision after
fresh Gemini/Jina calibration evidence exists. RAGAS, an LLM/NLI judge, network
use, credentials, paid calls, and new dependencies remain unapproved.
