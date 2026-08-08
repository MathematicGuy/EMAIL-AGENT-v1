# SPEC — RAG Golden Set & Retrieval Evaluation Pipeline

> **Status:** Draft, ready for implementation.
> **Created:** 2026-08-09
> **Closes:** [RAG-EVALUATION-STATUS.md](./RAG-EVALUATION-STATUS.md) gaps **C4** (real-embedding Hit@K / MRR), **C5** (email → corpus retrieval fixture), **C6** (hybrid retrieval benchmark).
> **Depends on:** nothing. Runs against the current dense-only `InRepoSemanticMemory`; the hybrid work in flight (`handoff-hybrid-search-rag.md`) consumes this as its scoreboard.
> **Deliberately out of scope for v1:** Layer 3 generation eval (D4/D5/D6) — specified in §9 with a trigger, not built now.

---

## 1. Problem

There is no number that says whether retrieval works. Every existing retrieval test uses `HashingEmbedder`, which validates mechanics (ACL, top-k, timeout, ordering) but proves nothing about semantic quality. The hybrid-search branch (BM25 + dense + RRF + Jina reranker) is being implemented right now with **no way to prove it is better than what it replaces**.

This spec defines the golden set and the harness that produce that number.

---

## 2. Corrections to prior docs (verified against code, 2026-08-09)

Three statements in `RAG-EVALUATION-STATUS.md` are stale or unimplementable. The design below works around all three.

| # | Claim in existing docs | Verified reality |
|---|---|---|
| 1 | Corpus is 3 documents including `dang_ky_tam_tru.md` | Corpus is **6 documents / 38 chunks**. `dang_ky_tam_tru.md` does not exist. Actual: `cap_lai_cccd` (5), `dang_ky_ket_hon` (7), `dang_ky_xe` (7), `huong_dan_nop_ho_so_dai_hoc_vinuni` (8), `thu_tuc_dang_ky_bhxh_luatvietnam` (7), `thue_dien_tu` (4). |
| 2 | Golden labels keyed on `cap_lai_cccd#Quy_trình_đăng_ký...` | `chunk_id` is **positional**: `f"{document_id}#{len(chunks)}"` → `cap_lai_cccd#0`…`#4` (`knowledge_base.py:79`). Section-slug chunk IDs do not exist. Labelling on raw `chunk_id` would silently break on every re-chunk. |
| 3 | Chunks map cleanly to H2 sections | `_split_sections` splits on **H2 only**. `cap_lai_cccd.md` has H1s at lines 115/144/166 that get swallowed into the single preceding H2, so chunks `#2`–`#4` carry the section label `"Quy trình đăng ký cấp lại CCCD online bằng VNeID"` while their text is about *cấp đổi/cấp lại cases*, *sáp nhập tỉnh*, and *nơi làm thẻ*. |

**Consequence of #2 and #3:** golden labels are keyed on **`document_id` + `section`**, resolved to `chunk_id`s at load time. `document_id` is the stable primary key; `section` is the finer, chunking-sensitive key. See §5.

**Consequence of #3 alone:** section-level scoring on `cap_lai_cccd` is unreliable until chunking also breaks on H1. Tracked as a separate task (§8, T-0) — not a blocker, because document-level metrics are unaffected.

**Post-T-0 update (landed):** `_split_sections` now matches `^#{1,2}\s+`. Corpus is **6 documents / 36 chunks**, and **no chunk carries `section is None`**. Per-document chunk counts are now `cap_lai_cccd` (6), `dang_ky_ket_hon` (6), `dang_ky_xe` (6), `huong_dan_nop_ho_so_dai_hoc_vinuni` (8), `thu_tuc_dang_ky_bhxh_luatvietnam` (7), `thue_dien_tu` (3). Wherever this document says 38 chunks, read 36.

**Fourth correction — section headings are not retrievable text.** `_split_sections` slices `raw_text[match.end():end]`, so the heading line is excluded from the chunk body and survives only in the `section` field. Any string appearing *only* in a heading is invisible to a retriever scoring chunk text. This rules out `Quyết định 1383/QĐ-BCA` as a `lexical` anchor for `dang_ky_xe` — it occurs only in that document's H1. Verified: it appears in zero chunk texts. `lexical` anchors must be confirmed present in `chunk.text`, not merely in the markdown source.

---

## 3. Goals & non-goals

**Goals**
- G1 — A committed, human-auditable golden set of realistic Vietnamese queries → expected corpus locations, covering all 6 documents.
- G2 — An offline harness producing Hit@1, Hit@3, MRR, Recall@5 at both document and section granularity.
- G3 — Metrics **sliced by probe type** so dense-only, BM25-only, and hybrid can be compared meaningfully (§4 — this is the part that makes the benchmark non-trivial).
- G4 — Runs deterministically offline (`--dry-run`, `HashingEmbedder`) in CI; runs live with `GeminiEmbeddingAdapter` on demand. Skips gracefully without API keys, exactly like `evaluate_routing.py`.
- G5 — One end-to-end integration test: a mocked email whose body asks a corpus question retrieves the right document (closes C5).

**Non-goals**
- No RAGAS, no LLM-as-judge, no faithfulness scoring in v1 (§9).
- No new dependency. `numpy` + stdlib only; the harness reuses `load_corpus`, `InRepoSemanticMemory`, and the existing embedders.
- No relevance *grades* (graded nDCG). Binary relevance only — 6 documents does not justify a graded scale.

---

## 4. The core design decision: probe types

With 38 chunks across 6 topically disjoint documents, **document-level Hit@1 saturates near 100% for any retriever**. A golden set of plain paraphrase queries would show dense, BM25, and hybrid all scoring ~1.0 and would prove nothing.

Every golden case therefore carries a `probe` tag that predicts *which retriever should win*:

| `probe` | Query construction | Expected behaviour | Why it is in the set |
|---|---|---|---|
| `lexical` | Contains an exact string only present in the corpus **chunk text**: a legal code (`Nghị định 69/2024/NĐ-CP`, `Nghị quyết 190/2025/QH15`), a portal URL, a form number (`Mẫu TK1-TS`) | **BM25 should win**; dense embeddings blur numeric identifiers | Proves the BM25 leg earns its place |
| `semantic` | Paraphrase with **near-zero token overlap** with the target chunk — different vocabulary for the same intent (e.g. *"vợ chồng muốn làm giấy tờ chính thức"* → marriage registration) | **Dense should win**; BM25 should miss | Proves the dense leg earns its place |

For `semantic`, raw token overlap is the wrong yardstick in Vietnamese: the corpus tokenizes to syllables, so `"nhân viên"` and `"viên chức"` share `viên` without sharing meaning, and every query scores 30–50 % nominal overlap. **Judge overlap by document frequency instead.** A shared syllable with `df ≥ 3` (of 6 documents) is noise BM25 down-weights anyway; a shared syllable with `df = 1` hands BM25 the target document for free and voids the probe. The authored set holds every `semantic` case to ≤ 2 shared tokens at `df ≤ 2`.
| `mixed` | Natural user phrasing — some shared terms, some paraphrase. The realistic majority | Hybrid ≥ max(dense, BM25) | The headline number |
| `unanswerable` | Plausible admin question the corpus genuinely cannot answer (e.g. company leave policy) | `retrieval_status == NO_RESULTS`, or all scores below `min_score` | Guards against a retriever that always returns something; the abstention metric |

**Acceptance for the hybrid branch (§7) is stated per probe type**, not on the aggregate. An aggregate that improves while `semantic` regresses is a failure, and only the sliced report can see that.

---

## 5. Golden set

### 5.1 Location & size

```
tests/fixtures/rag/
├── retrieval_golden.json   # the labeled cases
├── loader.py               # typed loader + schema validation
└── README.md               # schema doc
```

Mirrors `tests/fixtures/routing/` exactly — same three-file shape, same loader idiom, same "never real user data" rule.

**Size: 32 cases.**

| Document | `lexical` | `semantic` | `mixed` | Total |
|---|:--:|:--:|:--:|:--:|
| `cap_lai_cccd` | 1 | 1 | 3 | 5 |
| `dang_ky_ket_hon` | 1 | 1 | 3 | 5 |
| `dang_ky_xe` | 1 | 1 | 3 | 5 |
| `huong_dan_nop_ho_so_dai_hoc_vinuni` | 1 | 1 | 2 | 4 |
| `thu_tuc_dang_ky_bhxh_luatvietnam` | 1 | 1 | 3 | 5 |
| `thue_dien_tu` | 1 | 1 | 2 | 4 |
| — (`unanswerable`) | — | — | — | 4 |
| **Total** | **6** | **6** | **16** | **32** |

32 is the floor for the per-probe slices (6 cases) to be worth reading at all; a single case flip moves a 6-case slice by 17 %-points, so per-probe numbers are directional signals and the `mixed` slice (16 cases) is the one that gates. Grow to ~60 when the corpus exceeds 10 documents.

### 5.2 Case schema

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Unique, `q-NNN`. |
| `query` | string | yes | The retrieval query, as the classifier would emit it. Vietnamese, natural phrasing. |
| `probe` | enum | yes | `lexical` \| `semantic` \| `mixed` \| `unanswerable`. |
| `expected_document_ids` | string[] | yes | Corpus file stems. **Empty array** iff `probe == "unanswerable"`. Primary relevance key — stable across re-chunking. |
| `expected_sections` | string[] | yes | Exact H2 section titles as emitted by `load_corpus`. May be empty when the answer spans a whole document or the target is a `section: null` preamble chunk. Secondary, chunking-sensitive key. |
| `email_body` | string \| null | yes | Non-null on cases doubling as end-to-end email fixtures (§6.3). Synthetic. Never real user data. |
| `notes` | string | no | Why this case exists / which distractor it targets. Ignored by the harness. |

**Validation rules enforced by `loader.py` (all raise `RetrievalFixtureError`):**
1. `id` unique and matching `^q-\d{3}$`.
2. `probe` in the enum.
3. `expected_document_ids` empty **iff** `probe == "unanswerable"`.
4. Every `expected_document_ids` entry resolves against a real file in `data/extracted/`.
5. Every `expected_sections` entry matches a `section` actually produced by `load_corpus` for one of the `expected_document_ids`. **This is the anti-rot guard** — the golden set fails loudly the moment chunking changes rather than silently scoring zero.
6. At least one case per `probe` per document, per the §5.1 table.

Rule 5 is the reason the loader takes a corpus path and is not pure JSON parsing.

### 5.3 Worked examples

Verified against real corpus content:

```json
[
  {
    "id": "q-001",
    "query": "Tôi cần cấp lại CCCD qua VNeID thì làm các bước nào?",
    "probe": "mixed",
    "expected_document_ids": ["cap_lai_cccd"],
    "expected_sections": ["Quy trình đăng ký cấp lại CCCD online bằng VNeID"],
    "email_body": "Chào anh,\n\nThẻ căn cước của em sắp hết hạn. Nhờ anh hướng dẫn giúp em các bước cấp lại CCCD online qua VNeID với ạ.",
    "notes": "C5 end-to-end fixture. Distractor: dang_ky_xe also mentions dichvucong portal."
  },
  {
    "id": "q-007",
    "query": "Nghị định 69/2024/NĐ-CP về định danh và xác thực điện tử áp dụng thế nào cho hệ thống thuế?",
    "probe": "lexical",
    "expected_document_ids": ["thue_dien_tu"],
    "expected_sections": ["Thông tin chung"],
    "email_body": null,
    "notes": "Exact legal-code token. Dense embeddings blur digits; BM25 should rank #1."
  },
  {
    "id": "q-013",
    "query": "Hai vợ chồng sắp cưới muốn hoàn tất giấy tờ chính thức, phải chuẩn bị những gì?",
    "probe": "semantic",
    "expected_document_ids": ["dang_ky_ket_hon"],
    "expected_sections": ["Quy trình đăng ký kết hôn online"],
    "email_body": null,
    "notes": "Zero overlap with 'đăng ký kết hôn' phrasing in the chunk. BM25 expected to miss."
  },
  {
    "id": "q-021",
    "query": "Xe nhập khẩu thì cần bổ sung những giấy tờ gì khi đăng ký?",
    "probe": "mixed",
    "expected_document_ids": ["dang_ky_xe"],
    "expected_sections": ["Hồ sơ bổ sung đối với xe nhập khẩu"],
    "email_body": null,
    "notes": "Near-duplicate distractor: 'Hồ sơ bổ sung đối với xe sản xuất, lắp ráp trong nước'. Section-level metric is the discriminator here."
  },
  {
    "id": "q-029",
    "query": "Quy trình xin nghỉ phép năm của công ty như thế nào?",
    "probe": "unanswerable",
    "expected_document_ids": [],
    "expected_sections": [],
    "email_body": "Chào HR,\n\nEm muốn xin nghỉ phép năm 5 ngày vào tháng sau, nhờ chị hướng dẫn quy trình ạ.",
    "notes": "Corpus has no HR policy. Must return NO_RESULTS, not a confident wrong chunk."
  }
]
```

`q-021` is the shape that matters most: two sibling sections with near-identical wording, where document-level Hit@1 is trivially 1.0 and only section-level MRR separates the retrievers.

---

## 6. Evaluation pipeline

### 6.1 Harness — `scripts/evaluate_retrieval.py`

Mirrors `scripts/evaluate_routing.py`: same module docstring convention, `argparse`, graceful skip without API keys, JSON report to `docs/baselines/`.

```
CLI
  --dry-run              HashingEmbedder, deterministic, CI-safe. Default when no API key.
  --embedder {hashing,gemini}
  --retriever {dense,bm25,hybrid}   default: dense. bm25/hybrid available once the
                                    hybrid branch lands; unknown value → clean exit 2.
  --top-k N              default 5
  --min-score F          default 0.2 (InRepoSemanticMemory default)
  --output PATH          default docs/baselines/retrieval-eval-<YYYY-MM-DD>-<embedder>-<retriever>.json
  --fail-under-mrr F     non-zero exit when section-level MRR < F. For CI gating.
```

Flow:

```
load_golden_cases(fixtures) ──┐
load_corpus(data/extracted) ──┼─→ validate labels against real sections (rule 5)
                              │
                              ├─→ build retriever (InRepoSemanticMemory | hybrid), build_index() ONCE
                              │
                              └─→ for each case: retrieve() → score → per-case result
                                          │
                                          └─→ aggregate overall + per-probe + per-document
                                                       │
                                                       └─→ JSON report
```

`build_index()` is called exactly once for the whole run — 38 chunks, one embedding batch. A live Gemini run costs 1 corpus batch + 32 query embeddings.

### 6.2 Metrics

Ranked results are the returned `chunks` tuple, already score-descending.

**Document level** (primary; stable across re-chunking) — a result is relevant iff `chunk.document_id ∈ expected_document_ids`:
- `hit_at_1`, `hit_at_3` — fraction of cases with ≥1 relevant result in the top 1 / top 3.
- `mrr` — mean of `1 / rank_of_first_relevant`, contributing 0 when absent.
- `recall_at_5` — fraction of `expected_document_ids` present in the top 5.

**Section level** (discriminating; the one that gates) — relevant iff `document_id` matches **and** `chunk.section ∈ expected_sections`. Same four metrics. Cases with empty `expected_sections` are **excluded** from section metrics, and the report states the excluded count.

**Abstention** (over `unanswerable` cases only):
- `abstention_rate` — fraction returning `NO_RESULTS` or zero chunks above `min_score`.
- `false_answer_case_ids` — the cases that confidently returned something. Named explicitly, like `false_negative_case_ids` in the routing harness.

**Latency:** p50 / p95 of `response.latency_ms`. Reported, not gated — the reranker will change this and we want the number visible before someone is surprised by it.

Every metric is emitted **overall and per `probe`**. That slice is the deliverable.

### 6.3 Report shape

Metrics and identifiers only — never query text or chunk text, matching the routing harness's privacy rule.

```json
{
  "generated_at": "2026-08-09T...Z",
  "embedder": "hashing",
  "retriever": "dense",
  "corpus": {"document_count": 6, "chunk_count": 38, "corpus_dir": "data/extracted"},
  "limits": {"top_k": 5, "min_score": 0.2},
  "case_count": 32,
  "document_level": {"hit_at_1": 0.0, "hit_at_3": 0.0, "mrr": 0.0, "recall_at_5": 0.0},
  "section_level": {"hit_at_1": 0.0, "hit_at_3": 0.0, "mrr": 0.0, "recall_at_5": 0.0,
                    "excluded_case_count": 0},
  "by_probe": {
    "lexical":  {"case_count": 6,  "document_level": {}, "section_level": {}},
    "semantic": {"case_count": 6,  "document_level": {}, "section_level": {}},
    "mixed":    {"case_count": 16, "document_level": {}, "section_level": {}}
  },
  "by_document": {"cap_lai_cccd": {"case_count": 5, "mrr": 0.0}},
  "abstention": {"case_count": 4, "abstention_rate": 0.0, "false_answer_case_ids": []},
  "latency_ms": {"p50": 0, "p95": 0},
  "misses": [{"case_id": "q-013", "probe": "semantic",
              "expected_document_ids": ["dang_ky_ket_hon"],
              "returned_document_ids": ["dang_ky_xe", "thue_dien_tu"]}]
}
```

`misses` is the debugging affordance — document IDs only, no text — and is what someone actually reads when a number drops.

### 6.4 Tests

| Test file | What it proves |
|---|---|
| `tests/unit/scripts/test_evaluate_retrieval.py` | Metric math on hand-built ranked lists: MRR is `1/rank`; a miss contributes 0; `hit_at_3` boundary at rank 3 vs 4; `recall_at_5` with multi-document expectations; section metrics exclude empty-`expected_sections` cases; abstention counts `NO_RESULTS` and below-threshold alike. **No corpus, no embedder — pure functions.** |
| `tests/unit/fixtures/test_retrieval_golden.py` | Every §5.2 validation rule fires: duplicate `id`, bad `probe`, non-empty docs on `unanswerable`, unknown `document_id`, **section not present in the real corpus** (rule 5, the anti-rot guard), per-document probe coverage. |
| `tests/integration/email_action_plan/test_rag_retrieval_golden.py` | **Closes C5.** Cases with non-null `email_body` run the real workflow path: `FakeMailbox` email → `FakeRouteClassifier` → `RETRIEVE_RAG` → real `InRepoSemanticMemory` over the real corpus → assert the top chunk's `document_id` matches `expected_document_ids`. `HashingEmbedder` in CI. |

The C5 integration test is expected to have **known failures under `HashingEmbedder`** — hash vectors are not semantic. Those cases are marked `xfail(strict=False)` with the reason recorded, and pass under `--embedder gemini`. Do not weaken the assertion to make hashing pass; that would rebuild the exact blind spot this spec exists to remove.

---

## 7. Acceptance criteria

**Phase 1 — golden set + harness (this spec):**
- [ ] `retrieval_golden.json` has 32 cases meeting the §5.1 distribution; `loader.py` enforces all six §5.2 rules.
- [ ] `python scripts/evaluate_retrieval.py --dry-run` completes offline, needs no API key, writes a report matching §6.3.
- [ ] `python scripts/evaluate_retrieval.py --embedder gemini` runs live and skips cleanly (exit 0, explanatory message) when `GEMINI_API_KEY` is unset.
- [ ] Unit tests in §6.4 pass; `ruff` and `mypy` clean on the new files.
- [ ] A **baseline report for dense-only + Gemini** is committed to `docs/baselines/`. Without this the hybrid branch has nothing to beat.

**Phase 2 — hybrid gate (consumed by `handoff-hybrid-search-rag.md`):**

Hybrid ships only if, on the same golden set with the same embedder:
- [ ] `mixed` section-level MRR: **hybrid ≥ dense baseline** (no regression on the realistic majority).
- [ ] `lexical` section-level Hit@1: **hybrid > dense baseline** (the BM25 leg earns its place).
- [ ] `semantic` section-level Hit@1: **hybrid ≥ dense baseline − 0.05** (RRF must not drown the dense signal; the 5-point band is one case out of six).
- [ ] `abstention_rate` does not decrease (hybrid must not start confidently answering unanswerable queries — RRF fuses ranks, not scores, so a low-confidence match can surface at rank 1; this is the specific failure mode being guarded).
- [ ] Both baselines (dense, hybrid) committed side by side for comparison.

If a criterion fails, that is a result worth having — record it and decide, do not retune the golden set to pass.

---

## 8. Task breakdown

| # | Task | Dependency | Notes |
|---|---|---|---|
| T-0 | Fix `_split_sections` to break on H1 as well as H2 | — | Independent of this spec; §2 finding #3. Without it `cap_lai_cccd` section labels are wrong and its section-level numbers are noise. Do first — it changes chunk IDs, and doing it after the golden set is written forces a relabel. |
| T-1 | `tests/fixtures/rag/loader.py` + `README.md` | T-0 | Copy the `tests/fixtures/routing/loader.py` idiom. |
| T-2 | Author the 32 golden cases | T-0, T-1 | The only genuinely manual step. Needs someone who can read the Vietnamese corpus and construct real `semantic` probes — a paraphrase that still shares tokens is a wasted case. |
| T-3 | `scripts/evaluate_retrieval.py` | T-1 | Metric functions pure and separate from I/O, so T-4 needs no corpus. |
| T-4 | Unit tests (metrics + fixture validation) | T-3 | |
| T-5 | C5 integration test | T-2, T-3 | |
| T-6 | Capture and commit the dense + Gemini baseline | T-2…T-5 | Phase 1 exit. |

T-2 is the critical path and cannot be parallelised away — the harness is mechanical, the labels are the actual work.

---

## 9. Deferred: Layer 3 generation evaluation (D4/D5/D6)

Not built now, and this is a scope decision rather than an oversight. Retrieval is the measurable bottleneck and the in-flight branch needs a scoreboard this week; a faithfulness harness needs a judge model, a judge prompt, and its own calibration set, which is a comparable amount of work aimed at a gap nobody is currently editing code against.

When it is built, the golden set extends rather than being replaced: add `expected_plan_claims` (a list of facts the plan must contain) and `forbidden_claims` (facts the corpus does not support) to the cases that already carry `email_body`. Faithfulness then becomes a check over those two lists — no RAGAS dependency needed at this corpus size.

**Trigger to build it:** the first time a generated plan is observed citing a valid chunk while stating something the chunk does not say. That is the failure mode D4/D5 exist to catch, and one real instance justifies the harness better than any amount of anticipation.

---

*Related:*
- [RAG-EVALUATION-STATUS.md](./RAG-EVALUATION-STATUS.md) — the gap map this spec closes (note §2 corrections above)
- [EMAIL-RAG-STATUS.md](./EMAIL-RAG-STATUS.md) — architecture implementation status
- `scripts/evaluate_routing.py` — the harness idiom being mirrored
- `tests/fixtures/routing/` — the fixture layout being mirrored
