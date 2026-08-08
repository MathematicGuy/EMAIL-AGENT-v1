# PLAN — Remaining Email-RAG Evaluation Gaps (C7, D5, D6)

> **Implements:** [SPEC-rag-c7-d5-d6-evaluation.md](../specs/SPEC-rag-c7-d5-d6-evaluation.md)
> **Created:** 2026-08-09
> **Branch checkpoint:** `qoder/target-architecture` at `35fa54a`
> **Scope:** Evaluation evidence only. This plan does not change retrieval runtime,
> choose an abstention policy, or add an automated semantic judge.

## Outcome

Close the smallest defensible evaluation slices for the three remaining gaps:

- C7 records privacy-safe score provenance and simulates calibration gates without
  changing `HybridSemanticMemory` or any shared retrieval contract.
- D6 adds deterministic binary section Precision@5 beside existing Recall@5.
- D5 adds a synthetic, human-labelled claim/evidence aggregation smoke contract,
  explicitly retaining automated semantic faithfulness as open work.

The implementation is complete only when the status document distinguishes
mechanical evidence, manual evidence, and still-open production decisions.

## Pre-flight boundaries

- Preserve unrelated dirty files. At the planning checkpoint these are
  `docs/SPEC-Demo-Frontend.md`, `src/cowork_agent/app.py`, and
  `src/cowork_agent/gui/app.py`.
- Stage explicit paths only. Do not use whole-worktree staging.
- Reports may contain identifiers, enums, counts, booleans, and numeric scores;
  they must not contain queries, chunks, prompts, plan text, or email text.
- D5 fixtures use synthetic content only. Raw user email and attachment content
  must never be persisted or logged.
- Do not modify `HybridSemanticMemory`, `InRepoSemanticMemory`, `DigestWorker`,
  `SemanticRetrievalResponse`, provider code, or retrieval defaults.
- No RAGAS, LLM/NLI judge, dependency, network call, credential, or paid API use.
- The code graph may be stale for evaluator code. Search it first; if it returns no
  useful nodes, use scoped reads of the named script and tests.

## Dependency order and orchestration

```text
T1 score evidence -> T2 calibration sweeps -> C7 checkpoint
                                      |
                                      v
T3 D6 Precision@5 ----------------> D6 checkpoint
                                      |
                                      v
T4 D5 fixture/loader -> T5 D5 aggregation -> D5 checkpoint -> T6 reconciliation
```

Writes are serialized because all agents share one checkout. Subagents may run
parallel read-only audits or review completed diffs, but only one writer works at
a time. The orchestration agent owns pre-flight state checks, final diff review,
verification, documentation reconciliation, explicit staging, and commits.

---

## T1 — C7 score-evidence contract

**Size:** M
**Depends on:** approved spec only
**Likely files:**

- `scripts/evaluate_retrieval.py`
- `tests/unit/scripts/test_evaluate_retrieval.py`

### Change

Extend evaluator-only `CaseResult` with rank-ordered numeric scores and explicit
provenance supplied by evaluator configuration rather than inferred only from
returned chunks:

- `returned_scores: tuple[float, ...]`, parallel to returned IDs/sections;
- `configured_score_kind`: `dense_cosine`, `bm25`, `rrf`, or `jina`;
- `observed_score_kind`: the same enum or null when no score was returned;
- `reranker_requested: bool`;
- `reranker_applied: bool`.

Thread configured score kind and reranker intent from retriever construction into
`run_evaluation` and every case, including empty results. This keeps `NO_RESULTS`
provenance explicit without pretending an actual score existed.

Derive the score used for each returned chunk as follows:

- dense: `relevance_score`, kind `dense_cosine`;
- BM25 harness adapter: `relevance_score`, kind `bm25`;
- hybrid without an applied reranker: fused `relevance_score`, kind `rrf`;
- hybrid with a non-null `rerank_score` on returned chunks: `rerank_score`, kind
  `jina`.

If reranking was requested but the response fell back with no rerank scores,
record `reranker_requested=true`, `reranker_applied=false`, and
`observed_score_kind=rrf` when chunks exist; an empty response has
`observed_score_kind=null`. Treat reranking as all-or-none for one response and
reject returned chunks that mix null and non-null rerank scores instead of
silently comparing incompatible values.

Add pure helpers that expose top score, runner-up score, and delta. Empty results
produce null summaries; a one-result case has a top score but null runner-up and
delta. Serialize a closed `score_evidence.cases` array containing only `case_id`,
`probe`, configured/observed score kinds, reranker flags, rank-ordered scores, top
score, runner-up score, and delta.

### Tests first

- dense, BM25, RRF, and Jina provenance mapping;
- requested-but-not-applied reranker fallback;
- empty, one-result, and two-result score summaries;
- parallel tuple/finite-score validation and mixed-kind rejection;
- recursive closed report key/type schema plus forbidden text-bearing fields;
- existing ranking metrics unchanged when no simulated gate is applied.

### Acceptance criteria

- Every evaluated case has unambiguous configured provenance, while observed
  provenance is null rather than invented for empty results.
- Top/runner-up/delta are reproducible without query or chunk content.
- Existing Hit@K, MRR, Recall@5, abstention, and latency output remains compatible.
- No `src/` or runtime contract changes.

### Verify

```powershell
python -m pytest tests/unit/scripts/test_evaluate_retrieval.py -q
python -m ruff check scripts/evaluate_retrieval.py tests/unit/scripts/test_evaluate_retrieval.py
python -m mypy scripts/evaluate_retrieval.py
```

---

## T2 — C7 calibration sweeps and reporting

**Size:** L
**Depends on:** T1
**Likely files:**

- `scripts/evaluate_retrieval.py`
- `tests/unit/scripts/test_evaluate_retrieval.py`

### Change

Add pure, evaluation-only simulations for absolute-score and top-vs-runner-up
margin gates. Partition non-empty input by `observed_score_kind`; never place
values from different score spaces in one sweep. Each simulated gate affects only
matching-kind cases; other kinds remain unchanged, and originally abstained cases
remain abstained. Report the matching affected-population count and inherited
abstention IDs.

Absolute gate semantics:

- an originally empty/`NO_RESULTS` case remains abstained;
- otherwise abstain when `top_score < threshold`;
- equality passes, matching the current dense `min_score` boundary.

Margin gate semantics:

- an originally empty/`NO_RESULTS` case remains abstained;
- otherwise abstain when a defined `delta < threshold`;
- equality passes;
- null deltas are not converted to zero and do not independently trigger
  abstention; report their case IDs as `undefined_margin_case_ids`.

Generate deterministic candidate boundaries from the finite observed values in
each score kind: the minimum value as the equality/pass-all boundary, midpoints
between unique sorted values, and `math.nextafter(maximum, math.inf)` as the
upper boundary. For margin sweeps the upper boundary abstains all cases with a
defined margin; cases with undefined margins remain unchanged and are counted
explicitly. Keep the pure sweep helpers able to accept an explicit candidate list
for boundary-focused tests.

For every candidate report:

- unanswerable case count, abstention count/rate, and false-answer IDs;
- answerable case count, false-abstention count/rate, and affected case IDs;
- document and section metrics overall and per probe after the simulated gate;
- observed score kind, gate kind, threshold, affected-population count,
  inherited-abstention IDs, and undefined-margin IDs.

Add the sweeps to `build_report` under an explicitly evaluation-only key. Do not
add a runtime CLI default or select a recommended threshold.

### Tests first

- score equal to threshold passes for both gate kinds;
- below-threshold cases abstain;
- empty, one-result, missing-runner-up, and mixed-score-kind inputs;
- four unanswerable versus 28 answerable accounting;
- answerable false-abstention IDs and per-probe regression metrics;
- deterministic candidate ordering and JSON shape;
- simulated gates do not mutate the original `CaseResult` sequence.

### Acceptance criteria

- Absolute and margin sweeps are separated by score kind.
- Each sweep makes the answerable/unanswerable tradeoff inspectable by case and
  probe.
- No threshold is described as calibrated or production-ready.
- The report stays privacy-safe and offline-capable.

### Verify

```powershell
python -m pytest tests/unit/scripts/test_evaluate_retrieval.py -q
python -m ruff check scripts/evaluate_retrieval.py tests/unit/scripts/test_evaluate_retrieval.py
python -m mypy scripts/evaluate_retrieval.py
$retrievalReport = Join-Path ([System.IO.Path]::GetTempPath()) ("email-agent-rag-c7-{0}.json" -f [guid]::NewGuid())
python scripts/evaluate_retrieval.py --dry-run --output $retrievalReport
```

### C7 checkpoint

Review the diff before continuing. Confirm only evaluator/test files changed and
inspect the OS-temp dry-run JSON against a recursive exact key/type allowlist for
`score_evidence` and sweep objects. Keep targeted forbidden-field assertions as
defense in depth. Record that hashing dry-run evidence proves mechanics only. Do
not retain the dry-run artifact, run Gemini/Jina, or change runtime behavior.

---

## T3 — D6 deterministic binary section Precision@5

**Size:** M
**Depends on:** T1 data shape; independent of T2 calculations
**Likely files:**

- `scripts/evaluate_retrieval.py`
- `tests/unit/scripts/test_evaluate_retrieval.py`

### Change

Add a pure `binary_section_precision_at_k` helper using the existing v1 relevance
rule: a returned item is relevant only when both its document ID and section match
the case labels. At `k=5`, divide relevant returned chunks by the number of returned
chunks considered, not by five. No returned chunks scores `0.0`.

Exclude unanswerable cases and answerable cases with empty expected sections from
the answerable section macro. Report exact keys
`excluded_unanswerable_case_count` and
`excluded_empty_expected_sections_case_count`. Add
`binary_section_precision_at_5` overall and per probe beside existing section
Recall@5. Multiple chunks under one labelled section all count as relevant under
this v1 binary label contract.

### Tests first

- relevant chunks at ranks 1 and 5; rank 6 excluded;
- partial precision and zero results;
- duplicate chunks under one relevant section;
- right document/wrong section and right section/wrong document;
- macro aggregation, per-probe output, and excluded-case counts.

### Acceptance criteria

- Overall and per-probe binary section Precision@5 is present beside Recall@5.
- Existing fixture labels are unchanged.
- Naming does not claim RAGAS or exhaustive graded context relevance.
- Existing metrics remain unchanged.

### Verify

```powershell
python -m pytest tests/unit/scripts/test_evaluate_retrieval.py -q
python -m ruff check scripts/evaluate_retrieval.py tests/unit/scripts/test_evaluate_retrieval.py
python -m mypy scripts/evaluate_retrieval.py
$retrievalReport = Join-Path ([System.IO.Path]::GetTempPath()) ("email-agent-rag-d6-{0}.json" -f [guid]::NewGuid())
python scripts/evaluate_retrieval.py --dry-run --output $retrievalReport
```

### D6 checkpoint

Review metric denominators and the serialized dry-run report. Record D6 as a
deterministic binary section-label metric, not semantic or RAGAS coverage.

---

## T4 — D5 synthetic labelled fixture and loader

**Size:** M
**Depends on:** D5 option recorded in the approved spec
**Likely files:**

- `tests/fixtures/rag/faithfulness_golden.json` (new)
- `tests/fixtures/rag/faithfulness_loader.py` (new)
- `tests/unit/fixtures/test_faithfulness_golden.py` (new)
- `tests/fixtures/rag/README.md`

### Change

Define frozen fixture types and a strict loader for synthetic cases. Each case
contains synthetic email evidence, synthetic chunk evidence keyed by chunk ID, and
human-authored atomic claims. Each claim contains `claim_id`, `step_number`, claim
text, allowed evidence origins (`email` and/or declared chunk IDs), and expected
`supported`/`unsupported` verdict.

Validate case/claim uniqueness, positive step numbers, enum values, origin
referential integrity, and that unsupported claims have no asserted supporting
origin. Keep fixture text available only to the human-review fixture; downstream
reports serialize IDs and counts, never text.

The fixture must include:

- a low-overlap supported paraphrase;
- a fabricated approver, threshold, deadline, or document name;
- an email-supported uncited claim;
- an unsupported uncited company claim;
- a multi-citation claim.

### Tests first

- valid fixture loads deterministically;
- duplicate IDs, bad step numbers/verdicts, and unknown origins fail clearly;
- the required semantic edge-case inventory is present;
- fixture and tests contain synthetic content only.

### Acceptance criteria

- The fixture contains no real email or user data.
- Every verdict and allowed origin is human-authored and auditable.
- The loader performs validation only; it does not infer semantic support from
  lexical overlap.

### Verify

```powershell
python -m pytest tests/unit/fixtures/test_faithfulness_golden.py -q
python -m ruff check tests/fixtures/rag/faithfulness_loader.py tests/unit/fixtures/test_faithfulness_golden.py
python -m mypy tests/fixtures/rag/faithfulness_loader.py
```

---

## T5 — D5 labelled aggregation harness

**Size:** M
**Depends on:** T4
**Likely files:**

- `scripts/evaluate_faithfulness.py` (new)
- `tests/unit/scripts/test_evaluate_faithfulness.py` (new)

### Change

Add a pure fixture-label inventory/aggregation layer over loaded human verdicts
and a small offline CLI. This consumes no predicted verdict and evaluates no
generated plan. Report:

- total and supported claim counts plus `supported_claim_ratio`;
- unsupported claim IDs and step numbers in deterministic order;
- explicit zero-claim behavior (`supported_claim_ratio: null`);
- evidence-origin breakdown for `email`, chunk, and multi-origin claims;
- fixture version/case IDs and a fixed evidence label such as
  `human_labelled_smoke_contract`.

The CLI reads the committed synthetic fixture and prints a metadata-only JSON
report to stdout by default. It must not echo claim, email, chunk, or plan text. It
must not invoke a provider or claim to classify unseen prose. Enforce a recursive
closed key/type allowlist for the report, with a targeted forbidden-field denylist
as defense in depth.

### Tests first

- supported/unsupported totals and ratio;
- unsupported IDs and step ordering;
- zero-claim behavior;
- email, chunk, and multi-origin breakdown;
- deterministic serialization, recursive closed schema, and privacy denylist;
- `--help` and offline evaluation require no provider keys.

### Acceptance criteria

- The harness proves fixture-label inventory, claim accounting, and evidence
  attribution; its ratio is fixture composition, not generated-plan quality.
- Output explicitly identifies the evidence as human-labelled/manual and partial.
- No lexical heuristic, semantic model, provider call, or new dependency exists.

### Verify

```powershell
python -m pytest tests/unit/scripts/test_evaluate_faithfulness.py tests/unit/fixtures/test_faithfulness_golden.py -q
python -m ruff check scripts/evaluate_faithfulness.py tests/fixtures/rag/faithfulness_loader.py tests/unit/scripts/test_evaluate_faithfulness.py tests/unit/fixtures/test_faithfulness_golden.py
python -m mypy scripts/evaluate_faithfulness.py tests/fixtures/rag/faithfulness_loader.py
python scripts/evaluate_faithfulness.py
```

### D5 checkpoint

Review the generated/printed report for text leakage. Confirm the evidence label
cannot be interpreted as automated semantic faithfulness or live generator quality.

---

## T6 — Final verification and status reconciliation

**Size:** M
**Depends on:** T1–T5 and all three checkpoints
**Likely files:**

- `docs/evaluations/email-rag/RAG-EVALUATION-STATUS.md`
- optionally a metadata-only baseline JSON only if the existing evaluator convention
  requires retaining the offline dry run

### Change

Reconcile only claims supported by the completed evidence:

- C7: score evidence and evaluation-only calibration sweeps available; production
  abstention remains open pending fresh Gemini/Jina evidence and a separate policy.
- D6: deterministic binary section Precision@5 available; labels are intended
  target sections, not exhaustive relevance judgments or RAGAS.
- D5: human-labelled fixture inventory/aggregation contract available; generation
  faithfulness remains open/partial, and automated semantic coverage does not
  increase.

Update the detailed rows, summary table, Mermaid/overview text, and recommended
next steps consistently. Do not opportunistically resolve the separate stricter D4
real-corpus golden-triple caveat.

### Acceptance criteria

- Every status claim links to an implemented test, script, or retained report.
- No wording claims runtime abstention, live-judge proof, RAGAS, or automatic
  semantic grounding.
- The three evidence levels are visibly distinct.

### Final verification

Use an external `--basetemp` if the default Windows pytest temp root is denied.

```powershell
python -m pytest tests/unit/scripts/test_evaluate_retrieval.py tests/unit/scripts/test_evaluate_faithfulness.py tests/unit/fixtures/test_retrieval_golden.py tests/unit/fixtures/test_faithfulness_golden.py -q
python -m ruff check scripts/evaluate_retrieval.py scripts/evaluate_faithfulness.py tests/fixtures/rag/faithfulness_loader.py tests/unit/scripts/test_evaluate_retrieval.py tests/unit/scripts/test_evaluate_faithfulness.py tests/unit/fixtures/test_faithfulness_golden.py
python -m mypy scripts/evaluate_retrieval.py scripts/evaluate_faithfulness.py tests/fixtures/rag/loader.py tests/fixtures/rag/faithfulness_loader.py
$retrievalReport = Join-Path ([System.IO.Path]::GetTempPath()) ("email-agent-rag-final-{0}.json" -f [guid]::NewGuid())
python scripts/evaluate_retrieval.py --dry-run --output $retrievalReport
python scripts/evaluate_faithfulness.py
git diff --check
```

Expand to the full suite only if the focused checks fail for a shared-contract
reason. Since this plan intentionally changes no `src/` file or shared runtime
contract, evaluator-local proof is the primary gate.

## Commit sequence

Use explicit staging paths and verify the staged diff before every commit.

| # | Scope | Suggested message |
|---|---|---|
| 1 | T1–T2 plus C7 tests | `feat(eval): add retrieval score calibration evidence` |
| 2 | T3 plus D6 tests | `feat(eval): add binary section precision metric` |
| 3 | T4–T5 plus D5 tests | `feat(eval): add labelled faithfulness smoke contract` |
| 4 | T6 status reconciliation | `docs(eval): reconcile remaining RAG evidence gaps` |

Do not include unrelated dirty files. The orchestration agent may combine a task
with its directly dependent tests, but must not combine the later runtime policy
with this evaluation-only series.

## Deferred follow-on

After fresh live Gemini dense/hybrid and actual Jina reports exist, write a separate
spec for runtime abstention. That decision must state acceptable answerable
false-abstention and per-probe regression limits, handle reranker fallback
explicitly, and select gates only within one compatible score space.
