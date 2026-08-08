# PLAN — RAG Golden Set & Retrieval Evaluation Pipeline

> **Implements:** [SPEC-rag-golden-set-and-eval.md](./SPEC-rag-golden-set-and-eval.md)
> **Created:** 2026-08-09
> **Branch:** work on `qoder/target-architecture` (or a branch off it). The hybrid-search agent is editing `src/cowork_agent/integrations/rag/{bm25,rrf,jina_reranker,hybrid}.py` — **this plan touches none of those files**, so the two streams do not collide. The only shared file is `knowledge_base.py` (T-0).
> **Verified against code on 2026-08-09.** Numbers below are measured, not estimated.

---

## Pre-flight state (measured, not assumed)

| Fact | Value |
|---|---|
| Corpus | 6 documents, **38 chunks** under `data/extracted/` |
| `chunk_id` format | `f"{document_id}#{index}"` — positional (`knowledge_base.py:79`) |
| Chunks with `section is None` | 6 of 38 (one title-only preamble chunk per document) |
| Existing RAG unit tests | **20 pass, 1 fails** — `test_load_corpus_reads_the_three_committed_documents` expects `dang_ky_tam_tru`, which no longer exists. **Pre-existing failure on this branch, not caused by this work.** |
| `mypy` config | `files = ["src"]` — `scripts/` and `tests/` are *not* type-checked by default; must be named explicitly |
| `pytest` config | `pythonpath = ["src"]`, no `pytest-asyncio`. Async code is driven with `asyncio.run(...)` inside sync test functions (see `tests/unit/integrations/rag/test_rag.py`) |
| Dependencies | No new ones. `numpy` is already imported by `memory.py`; stdlib covers the rest |

---

## Task order

T-0 must land first: it changes every `chunk_id`, so any golden set authored before it would need a full relabel. T-1 and T-3 are independent of each other and can interleave. T-2 is the critical path.

```
T-0 (chunking fix) ──┬─→ T-1 (loader) ──→ T-2 (author cases) ──┬─→ T-5 (integration test) ──→ T-6 (baseline)
                     └─→ T-3 (harness) ──→ T-4 (unit tests) ───┘
```

---

## T-0 — Split sections on H1 as well as H2

**Files:** `src/cowork_agent/integrations/rag/knowledge_base.py`, `tests/unit/integrations/rag/test_rag.py`

**Why:** `_split_sections` matches H2 only, so `cap_lai_cccd.md`'s three H1s (lines 115/144/166) are swallowed into the preceding H2. Chunks `#2`–`#4` are labelled *"Quy trình đăng ký cấp lại CCCD online bằng VNeID"* while their text is about *cấp đổi cases*, *sáp nhập tỉnh*, and *nơi làm thẻ*. Section-level scoring on that document would be measuring a labelling bug. Same defect hides `thue_dien_tu.md`'s H1 at line 41.

**Change:** one pattern.

```python
# knowledge_base.py — replace the H2-only section pattern
_SECTION_PATTERN = re.compile(r"^#{1,2}\s+(.+)$", re.MULTILINE)
```

`_H1_PATTERN` stays as-is for title extraction; only `_split_sections` switches to `_SECTION_PATTERN`.

**Measured effect (prototyped, not predicted):**

| | Before | After |
|---|:--:|:--:|
| Total chunks | 38 | **36** |
| Chunks with `section is None` | 6 | **0** |
| `cap_lai_cccd` distinct sections | 1 (+1 null) | **4** |
| `thue_dien_tu` chunks | 4 | 3, with the previously-hidden *"Hướng dẫn nộp tờ khai thuế online…"* section now addressable |

Title-only preamble chunks disappear (their body is empty once the H1 becomes a heading), which removes 6 chunks of pure noise. All 36 remaining chunks carry a real section label.

**Also fix in the same commit** (it is the same root cause — stale corpus assumptions):
`test_load_corpus_reads_the_three_committed_documents` → rename to `test_load_corpus_reads_the_committed_documents` and assert the real six IDs. Check `test_load_corpus_chunks_by_h2_sections` and `test_ranking_min_score_and_top_k` still hold; they use `tmp_path` and a CCCD query respectively and are expected to survive, but confirm rather than assume.

**Done when:** `python -m pytest tests/unit/integrations/rag/ -q` is **21 passed, 0 failed**.

---

## T-1 — Golden-set loader

**Files (new):** `tests/fixtures/rag/loader.py`, `tests/fixtures/rag/README.md`

Mirror `tests/fixtures/routing/loader.py` exactly: module docstring naming the consumer, frozen dataclasses, `_require_str` / `_optional_str` / `_enum_value` helpers, a single `*FixtureError(ValueError)`, `DEFAULT_FIXTURE_PATH = Path(__file__).with_name(...)`.

```python
class Probe(StrEnum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    MIXED = "mixed"
    UNANSWERABLE = "unanswerable"

class RetrievalFixtureError(ValueError): ...

@dataclass(frozen=True)
class RetrievalCase:
    id: str
    query: str
    probe: Probe
    expected_document_ids: tuple[str, ...]
    expected_sections: tuple[str, ...]
    email_body: str | None
    notes: str | None

def load_retrieval_golden(
    path: Path | None = None, *, corpus_dir: Path | None = None
) -> tuple[RetrievalCase, ...]:
    """Parse and validate. When corpus_dir is given, also enforce rules 4-6."""
```

Validation rules — SPEC §5.2, all raising `RetrievalFixtureError` with a `path[index]:` prefix:

1. `id` unique, matches `^q-\d{3}$`.
2. `probe` in the enum.
3. `expected_document_ids` empty **iff** `probe == UNANSWERABLE`.
4. every `expected_document_ids` entry is a real `data/extracted/*.md` stem.
5. every `expected_sections` entry equals a `section` that `load_corpus` actually emits for one of that case's `expected_document_ids`.
6. per-document probe coverage matches the SPEC §5.1 table.

Rules 4–6 need the corpus, which is why `corpus_dir` is a parameter. **Keep it optional** so pure-schema tests (rules 1–3) run without touching the filesystem — the harness always passes it.

Rule 5 is the anti-rot guard and the reason this loader exists at all: without it, a re-chunk silently scores 0.0 instead of failing loudly.

`README.md`: copy the routing README's field table + "never real user data" rule, restated for this schema.

**Done when:** loader imports clean; `ruff check` and `mypy tests/fixtures/rag/loader.py` pass.

---

## T-2 — Author the 32 golden cases

**File (new):** `tests/fixtures/rag/retrieval_golden.json`

**This is the critical path and the only genuinely manual task.** The harness is mechanical; the labels are the work. Distribution per SPEC §5.1: 6 `lexical`, 6 `semantic`, 16 `mixed`, 4 `unanswerable`.

Authoring rules, in priority order:

1. **`semantic` cases must have near-zero token overlap with the target chunk.** A paraphrase that still contains *"đăng ký kết hôn"* is a wasted case — BM25 will find it and the case proves nothing. Write the query the way a user who does not know the official term would write it.
2. **`lexical` cases must contain a string that appears verbatim in exactly one chunk.** Harvest real ones from the corpus: `Nghị định 69/2024/NĐ-CP`, `Thông tư 86/2024/TT-BTC`, `Quyết định 1383/QĐ-BCA`, `Quyết định 1335/QĐ-TCT`, `Nghị quyết 202/2025/QH15`, the `dichvucong.dancuquocgia.gov.vn` portal URL. Verify uniqueness with `grep` before committing the label.
3. **Prefer targets with a near-duplicate sibling section.** `dang_ky_xe`'s *"Hồ sơ bổ sung đối với xe nhập khẩu"* vs *"…xe sản xuất, lắp ráp trong nước"* is the ideal shape: document-level Hit@1 is trivially 1.0, so only section-level MRR separates retrievers. Cases without a distractor contribute almost nothing.
4. **Set `email_body` on 8–10 cases** spread across all 6 documents plus at least one `unanswerable`. These become the T-5 end-to-end fixtures. Synthetic Vietnamese, realistic register, never real user data.
5. **`expected_sections` must be copy-pasted from `load_corpus` output after T-0**, not from the markdown source. They must match byte-for-byte or rule 5 rejects them.

Generate the section vocabulary to copy from:

```bash
python -c "
from pathlib import Path
from cowork_agent.integrations.rag.knowledge_base import load_corpus
for d in load_corpus(Path('data/extracted'), tenant_id='local'):
    print(d.document_id)
    for s in dict.fromkeys(c.section for c in d.chunks):
        print('   ', s)
"
```

SPEC §5.3 has five worked examples (`q-001`, `q-007`, `q-013`, `q-021`, `q-029`) — reuse them verbatim as the pattern for the remaining 27.

**Done when:** `load_retrieval_golden(corpus_dir=...)` returns 32 cases with no error.

---

## T-3 — Evaluation harness

**File (new):** `scripts/evaluate_retrieval.py`

Mirror `scripts/evaluate_routing.py`: docstring stating the obligation and the regenerate/smoke commands, `argparse`, graceful skip without API keys, JSON report under `docs/baselines/`, **identifiers and metrics only — never query or chunk text**.

CLI per SPEC §6.1: `--dry-run`, `--embedder {hashing,gemini}`, `--retriever {dense,bm25,hybrid}`, `--top-k`, `--min-score`, `--output`, `--fail-under-mrr`.

**Structure the module so metrics are pure and I/O-free** — T-4 must test them without a corpus or an embedder:

```python
# ---- pure: no corpus, no embedder, no filesystem ----
@dataclass(frozen=True)
class CaseResult:
    case_id: str
    probe: str
    expected_document_ids: tuple[str, ...]
    expected_sections: tuple[str, ...]
    returned_document_ids: tuple[str, ...]     # rank-ordered
    returned_sections: tuple[str | None, ...]  # rank-ordered, parallel to above
    retrieval_status: str
    latency_ms: int

def rank_of_first_relevant(result: CaseResult, *, level: str) -> int | None: ...
def reciprocal_rank(rank: int | None) -> float: ...          # 0.0 when None
def hit_at_k(results, k, *, level) -> float: ...
def recall_at_k(results, k, *, level) -> float: ...
def aggregate(results) -> dict: ...                          # overall + by_probe + by_document
def abstention_stats(results) -> dict: ...

# ---- impure: corpus, embedder, report ----
async def run_evaluation(...) -> list[CaseResult]: ...
def build_report(...) -> dict: ...
def main() -> int: ...
```

Implementation notes that matter:

- **`build_index()` exactly once** per run, before the case loop. 36 chunks = one embedding batch; a live Gemini run costs 1 corpus batch + 32 query embeddings.
- **Section-level metrics exclude cases with empty `expected_sections`**, and the report states `excluded_case_count`. After T-0 this should be 0 for `mixed`/`lexical`/`semantic`, but do not hard-code that — future documents may reintroduce null sections.
- **Abstention** counts a case as abstaining when `retrieval_status == NO_RESULTS` **or** zero chunks came back. Both are correct behaviour; only "returned a confident chunk for an unanswerable query" is a failure.
- ~~`--retriever bm25|hybrid` before those modules exist: exit **2** with a clear message.~~ **Superseded 2026-08-09: hybrid search has landed** (commit `ccf6539`, see `HYBRID-SEARCH-IMPLEMENTATION-REPORT.md`). Wire all three retrievers for real:
  - `dense` → `InRepoSemanticMemory(documents, embedder)`
  - `hybrid` → `HybridSemanticMemory(documents, embedder, reranker=None)` — same `build_index()` / `retrieve()` interface, so it drops straight into the same code path
  - `hybrid+rerank` → `HybridSemanticMemory(..., reranker=JinaRerankerAdapter(...))`
  - `bm25` → needs a thin harness-local shim: `BM25SearchAdapter.search(query, *, tenant_id, top_k)` returns `tuple[tuple[str, float], ...]`, **not** a `SemanticRetrievalResponse`. Map chunk IDs back through a `{chunk_id: chunk}` dict and synthesise the response so all four variants share one measurement path. Keep the shim in the script, not in `src/` — it exists only to make BM25 measurable in isolation.
- `--fail-under-mrr` gates on **section-level** MRR (the discriminating metric), not document-level.
- Report to `docs/baselines/retrieval-eval-<YYYY-MM-DD>-<embedder>-<retriever>.json`, shape per SPEC §6.3.

**Done when:** `python scripts/evaluate_retrieval.py --dry-run` writes a valid report with no API key set.

---

## T-4 — Unit tests

**Files (new):** `tests/unit/scripts/test_evaluate_retrieval.py`, `tests/unit/fixtures/test_retrieval_golden.py`

Load the script the way `tests/unit/scripts/test_evaluate_routing.py` does — `importlib.util.spec_from_file_location` plus the `sys.modules[spec.name] = module` line (dataclass annotations resolve through `sys.modules`; omitting it breaks at import).

**Metric tests** — hand-built `CaseResult`s, no corpus, no embedder:

| Test | Asserts |
|---|---|
| MRR is `1/rank` | relevant at rank 3 → 1/3 |
| A miss contributes 0 | no relevant result → 0.0, not excluded from the mean |
| `hit_at_3` boundary | relevant at rank 3 counts; at rank 4 does not |
| `recall_at_5` multi-doc | 1 of 2 expected documents in the top 5 → 0.5 |
| Section level is stricter | right document, wrong section → document-level hit, section-level miss |
| Section exclusion | empty `expected_sections` → excluded from section metrics, counted in `excluded_case_count` |
| Abstention | `NO_RESULTS` and zero-chunks both count as abstaining; a returned chunk lands in `false_answer_case_ids` |
| `--help` runs with no provider keys | mirrors `test_help_runs_without_provider_keys` |
| Dry-run writes a report | mirrors `test_dry_run_writes_report_without_provider_keys`; assert the report contains **no query text** |

**Fixture-validation tests** — each of the six rules fires, one test apiece, against tiny inline JSON in `tmp_path`. Rule 5 (section not present in the real corpus) is the important one; it is what stops the golden set rotting silently.

**Done when:** `python -m pytest tests/unit/scripts/ tests/unit/fixtures/ -q` is green.

---

## T-5 — End-to-end email→retrieval test (closes C5)

**File (new):** `tests/integration/email_action_plan/test_rag_retrieval_golden.py`

For every golden case with a non-null `email_body`: build a `FakeMailbox` email from it, route via `FakeRouteClassifier` to `RETRIEVE_RAG` with the case's `query` as `retrieval_query`, run a real `InRepoSemanticMemory` over the real corpus, and assert the top chunk's `document_id` is in `expected_document_ids`.

Copy the wiring from `tests/integration/email_action_plan/test_workflow.py` — it already builds this exact graph with `RecordingMemory`; swap in the real memory.

**Expect known failures under `HashingEmbedder`.** Hash vectors are not semantic; some cases will not pass offline. Mark those `@pytest.mark.xfail(strict=False)` with the reason recorded in the marker, and confirm they pass under `--embedder gemini`.

> Do **not** weaken the assertion to make hashing pass. Softening it to "the right document appears somewhere in the top 5" rebuilds the exact blind spot this whole spec exists to remove: a green suite that proves nothing about semantic quality.

**Done when:** the suite is green with the xfail set documented, and a live Gemini run converts them to passes.

---

## T-6 — Capture the baseline (Phase 1 exit)

**Scope changed 2026-08-09.** Hybrid search landed before this harness did, so T-6 is no longer "capture a baseline for a future branch to beat" — it is the comparison itself, run in one sitting:

```bash
python scripts/evaluate_retrieval.py --embedder gemini --retriever dense
python scripts/evaluate_retrieval.py --embedder gemini --retriever bm25
python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid
python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid --rerank   # JINA_API_KEY is set
git add docs/baselines/retrieval-eval-*.json
```

Credentials are present: `GEMINI_API_KEY_1..6` in `.env` (read via `config.py` → `GeminiSettings.api_keys`) and `JINA_API_KEY` in the environment. Cost per variant is 1 corpus batch + 32 query embeddings; BM25 costs nothing.

Read the result against SPEC §7 **per slice**, and expect the probe design to be falsifiable here:

- `lexical` — BM25 ≥ dense. If dense already wins this slice, the BM25 leg is not earning its place and the report should say so.
- `semantic` — dense ≥ BM25. If BM25 wins, the semantic cases leak tokens and need rewriting before any conclusion is drawn.
- `mixed` — hybrid ≥ max(dense, BM25). This is the headline. **Hybrid winning the aggregate while losing `semantic` is a failure**, and it is exactly what RRF over a weak dense leg looks like.
- `unanswerable` — abstention must not regress. BM25 always returns something for a query with any matching token, so this is the slice where hybrid is most likely to get *worse* than dense.

Also capture the `--dry-run` hashing report for CI drift detection.

### Pre-flight measurement — BM25 leg alone (2026-08-09, no API cost)

Measured by driving `BM25SearchAdapter` directly over the 32 authored cases, `top_k=5`, before the harness existed. This is the BM25 column of the T-6 table, already known:

| slice | n | doc Hit@1 | section MRR |
|---|--:|--:|--:|
| `lexical` | 6 | **1.00** | 0.92 |
| `semantic` | 6 | 0.67 | **0.38** |
| `mixed` | 16 | 0.88 | 0.91 |
| `unanswerable` | 4 | — | abstention **0.00** |

Three things this already settles:

1. **The probe tags are predictive, so the set is doing its job.** BM25 tops out on `lexical` and collapses to 0.38 section MRR on `semantic` — a 0.54 spread between slices that the aggregate would have hidden entirely.
2. **`semantic` section MRR = 0.38 is the bar dense must clear.** If Gemini dense does not beat it there, the dense leg is not earning its place and hybrid is just BM25 with overhead.
3. **Abstention is the slice most at risk from hybrid.** Raw BM25 abstains on 0/4 unanswerable queries — it returns something for any query sharing a single token. Note this measures the raw leg without `min_score` applied, so it is a ceiling on the problem, not the final number; but if hybrid's abstention comes in below dense's, RRF pulling up BM25's always-present candidates is the cause. Report abstention per retriever, not just retrieval accuracy.

Document-level Hit@1 is 0.67–1.00 across every slice, which is the saturation SPEC §4 predicted. **Read section-level MRR as the headline; document-level is nearly uninformative on this corpus.**

Then update `RAG-EVALUATION-STATUS.md`: C4/C5 → ✅ with the harness path, C6 → ✅ or ❌ **with the measured numbers** rather than ⏳, and correct the stale claims listed in SPEC §2.

---

## Verification gate (run before declaring done)

```bash
python -m pytest tests/unit/integrations/rag/ tests/unit/scripts/ tests/unit/fixtures/ -q
python -m pytest tests/integration/email_action_plan/ -q
python -m pytest -q                     # full suite, no regressions
python -m ruff check scripts/ tests/fixtures/rag/ src/cowork_agent/integrations/rag/
python -m mypy scripts/evaluate_retrieval.py tests/fixtures/rag/loader.py
python scripts/evaluate_retrieval.py --dry-run
```

`mypy` needs the explicit file arguments — the project config is `files = ["src"]`, so neither `scripts/` nor `tests/` is checked otherwise.

---

## Commit sequence

| # | Scope | Message |
|---|---|---|
| 1 | T-0 | `fix(rag): chunk sections on H1 as well as H2` |
| 2 | T-1 | `test(rag): golden-set loader with corpus-backed validation` |
| 3 | T-2 | `test(rag): 32-case retrieval golden set` |
| 4 | T-3 + T-4 | `feat(eval): retrieval evaluation harness with per-probe metrics` |
| 5 | T-5 | `test(rag): end-to-end email to corpus retrieval fixtures` |
| 6 | T-6 | `docs(eval): dense+gemini retrieval baseline` |

Commit 1 stands alone — it is a real bug fix and is worth landing even if the rest slips.

---

## Risks

| Risk | Mitigation |
|---|---|
| T-0 collides with the hybrid agent if they also touch `knowledge_base.py` | Land commit 1 early and tell them. Their handoff scopes them to `bm25/rrf/jina_reranker/hybrid.py`, so a collision is unlikely but not impossible. |
| T-2 labels drift from a later re-chunk | Loader rule 5 fails loudly instead of scoring 0.0 silently. This is the whole reason rule 5 exists. |
| `semantic` cases accidentally share tokens, so BM25 wins and the slice proves nothing | Check each `semantic` query against its target chunk for overlapping content words before committing. |
| 6-case probe slices are noisy — one flip moves a slice 17 %-points | Stated in SPEC §5.1. Per-probe numbers are directional; the 16-case `mixed` slice is what gates. |
| Live Gemini run is non-deterministic across model versions | Report records `embedder` and `generated_at`; compare like-for-like only, and re-baseline when the embedding model changes. |
