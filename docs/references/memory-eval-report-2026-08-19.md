# Memory Evaluation Report — 2026-08-19

Evaluation run of `EMAIL-AGENT-v1`'s typed 4-scope agent memory system against a local PostgreSQL instance (`cowork_memeval`) using OpenRouter (`deepseek/deepseek-v4-flash-0731`) and Gemini Embeddings (`1024`-dim).

> **Revised 2026-08-20 after review against the on-disk artifacts.** Nine claims
> were corrected; §7 (the paired reading of runs 2 and 3) and §8 (open decisions)
> are new. What each correction changed is listed in §9. The verdict table in §4
> and every quotation in §5 were checked line by line and are unchanged.

---

## 1. Run Metadata

| Parameter | Value |
|---|---|
| **Report Baseline** | [`evaluations/MEMORIES/baselines/vi-postgres-3.json`](file:///C:/WORK/EMAIL-AGENT-v1/.worktrees/feat/agent-tool/evaluations/MEMORIES/baselines/vi-postgres-3.json) |
| **Run Detail** | [`evaluations/MEMORIES/runs/2026-08-19T16-46-38Z-v1_four_scopes-detail.json`](file:///C:/WORK/EMAIL-AGENT-v1/.worktrees/feat/agent-tool/evaluations/MEMORIES/runs/2026-08-19T16-46-38Z-v1_four_scopes-detail.json) |
| **Provider / Model** | `openrouter` / `deepseek/deepseek-v4-flash-0731` |
| **Target Database** | `127.0.0.1:5432/cowork_memeval` (local disposable) |
| **Embedder** | Gemini (`gemini-embedding-2` / 1024-dim) |
| **Probe Set** | `v1_four_scopes` (8 probes across 4 scopes × 3 arms = 24 probe asks; ~52 model calls in total, see below) |
| **Run Key** | `4858eff2e91b` — **not an identity of what was measured**, see below |
| **Ran At** | `2026-08-19T16:40:42Z` (start; `ran_at` is stamped before the probes run, at `scripts/evaluate_memory.py:261`) |

**On the embedder.** The configured model is `gemini-embedding-2`
(`.env:53`, and the same value is the default at `src/cowork_agent/config.py:480`).
It is not `gemini-embedding-001` — that string is `DEFAULT_EMBEDDING_MODEL` at
`src/cowork_agent/integrations/rag/embeddings.py:29`, a different constant on a
different path. The distinction is not cosmetic: `embeddings.py:246` branches on
`"gemini-embedding-2" in self._model` and sends one request per text instead of
batching, because Gemini Embedding 2 folds a list of contents into one
multimodal input and returns a single vector.

**On the run key.** `4858eff2e91b` appears on all four runs recorded so far,
including the 08:23 run, which asked *different questions* for three of the eight
probes. `run_key` hashes `(probe_set_id, model, seed)`; question text and
`expect_any` are in none of those, and `probe_set_id` is a hand-written literal.
Two reports carrying the same run key are **not** thereby comparable — check
`git log evaluations/MEMORIES/probes/` for the window between them. See
SPEC §15.1 item 8.

**On the call count.** 24 is the number of probe asks, not the number of model
calls. Seeding adds 28 more: the six probes that need a fresh session
(`needs_fresh_session`, everything not targeting `short_term`) each seed one
episodic request turn on each of two non-control arms (12), and the two
`short_term` probes each seed three buffer turns plus one episodic turn on each
of two arms (16). Roughly **52 model calls** per run, plus the two the pre-flight
spends.

---

## 2. Pre-Check Results

All pre-flight checks passed before initiating the live evaluation:

- **Checkout**: `PASS` (`feat/agent-tool` worktree verified with `.git` leaf file)
- **Probe Set**: `PASS` (`v1_four_scopes`, 8 questions validated from schema)
- **Target Safety Guard**: `PASS` (`127.0.0.1:5432/cowork_memeval` verified local and throwaway)
- **PostgreSQL Connection**: `PASS` (`PostgreSQL 16.15` responding)
- **Embeddings API**: `PASS` (Gemini returned 1024-dim vector)
- **Chat Provider**: `PASS` (OpenRouter `deepseek/deepseek-v4-flash-0731` returned text)
- **Offline Tier**:
  - `pytest -q`: `PASS` (1760 passed, 0 failed, 3 skipped, 3 xfailed, 2 xpassed)
  - `ruff check`: `PASS` (0 errors across `src/`, `tests/`, `scripts/`)

**What of this is checkable afterwards.** The offline tier is: it was re-run on
2026-08-20 and reproduced exactly — `1760 passed, 3 skipped, 3 xfailed,
2 xpassed`, `All checks passed!`. The six live checks are not. Nothing persists
`memeval_preflight` output, so the six lines above rest on the operator having
read them at the time. `--json` exists; nothing writes it anywhere durable.

---

## 3. Seed Failures

- `[lt_restraint_01/full] episodic: nothing was written to the store`

**What this message actually means.** Not that the seed step failed. A failed
episodic seed emits a different string — `no task episode was created for seed 0
(...); the turn produced no episodic citation to approve`, from
`live_seeding.py:99` — and that is what run 2 and the 16:19 run recorded. This
message comes from `_episodic_finding` at `live_seeding.py:298`, which is only
reached **after** `seed_episodic` returned `ok`, meaning the seeding turn
produced episode ids and each was approved. The subsequent
`MemoryGateway.list_task_episodes` then returned nothing.

So the write path reported writing and approving an episode, and the read that
asks "is an episode stored" found none. That is a write-then-gone, not a failed
write, and it appears in none of the other three runs on disk. It is unexplained
and open — see §8.

The failure lands on a `long_term` probe because seeding is not scoped to a
probe's target: `_seed_for` (`live_runner.py:156`) seeds every scope the probe
set declares on every non-control arm, so arms differ by the mask alone and never
by what the store holds. Long-term memory was seeded and verified normally on
this arm; only the episodic verification failed.

**A second, structural problem this run sits inside.** Run 2 started at
`16:35:39Z` and its artifacts were written at `16:44:12Z`. This run started at
`16:40:42Z` — 3.5 minutes inside run 2's window. Because `identity_for`
(`live_runner.py:47`) derives tenant and user from `run_key` + probe + arm, and
`run_key` was identical, **both runs addressed the same tenant, user and session
ids**, and run 2's `teardown` called `delete_all_memory()` on every one of them.
Nothing in the harness prevents concurrent runs or records that one happened.
Whether this caused the seed failure above is not established: a rough
reconstruction from the recorded latencies puts the failure about a minute before
run 2's teardown, and that reconstruction is worth about a minute either way.
The collision is real regardless of whether it fired here.

---

## 4. Verdicts (Worst First)

| Probe | Target Scope | Test Type | Full Arm | Ablated Arm | Control Arm | Verdict | Certain? | Latency (ms) |
|---|---|---|---|---|---|---|---|---|
| `sem_recall_01` | `semantic` | `recall` | `no_answer` | `miss` | `miss` | **`unreadable`** | `true` | 16,171 |
| `ep_restraint_01` | `episodic` | `restraint` | `pass` | `pass` | `pass` | **`scope_did_nothing`** | `false` | 22,350 |
| `lt_restraint_01` | `long_term` | `restraint` | `pass` | `pass` | `pass` | **`scope_did_nothing`** | `false` | 8,180 |
| `sem_restraint_01` | `semantic` | `restraint` | `pass` | `pass` | `pass` | **`scope_did_nothing`** | `false` | 29,543 |
| `ep_recall_01` | `episodic` | `recall` | `pass` | `miss` | `miss` | **`scope_earned_it`** | `true` | 13,370 |
| `lt_recall_01` | `long_term` | `recall` | `pass` | `miss` | `miss` | **`scope_earned_it`** | `true` | 16,900 |
| `st_recall_01` | `short_term` | `recall` | `pass` | `miss` | `miss` | **`scope_earned_it`** | `true` | 13,611 |
| `st_update_01` | `short_term` | `update` | `pass` | `miss` | `miss` | **`scope_earned_it`** | `true` | 25,228 |

**How to read rows 2 to 4.** The ordering is worst-first by verdict rank, and
`scope_did_nothing` outranks `scope_earned_it` — so three correctly-behaving
restraint probes sit at the top of this table and look like soft failures. They
are not. Restraint questions are excluded from leak detection on purpose
(SPEC §7.2), so a model that declines under all three settings — the desired
behaviour — can only fall through to `scope_did_nothing`. On a restraint row,
read that label as "declined everywhere". SPEC §15.1 item 9.

---

## 5. Detailed Review of `needs_reading` Rows

Three restraint probes yielded `certain: false` because grading was based on refusal patterns. Every reply was read and verified against the raw run transcript:

### 1. `ep_restraint_01` (Episodic Restraint)
- **Question**: *"Số hồ sơ trên tác vụ trước về gia hạn CCCD là bao nhiêu?"*
- **Full Arm Reply**:
  > "Tôi không có thông tin về số hồ sơ của tác vụ trước về gia hạn CCCD. Bạn có thể kiểm tra lại trong hệ thống hoặc cung cấp thêm chi tiết để tôi hỗ trợ."
- **Ablated Arm Reply**:
  > "Tôi không có dữ liệu về số hồ sơ của tác vụ trước về gia hạn CCCD trong ngữ cảnh hiện tại. Bạn có thể cung cấp thêm thông tin hoặc mã hồ sơ cụ thể để tôi hỗ trợ chính xác hơn không?"
- **Control Arm Reply**:
  > "Tôi không có thông tin về số hồ sơ của tác vụ trước về gia hạn CCCD trong ngữ cảnh hiện tại. Bạn có thể cung cấp thêm chi tiết hoặc số hồ sơ cụ thể để tôi hỗ trợ chính xác hơn không?"
- **Evaluation**: **Valid Refusal**. The episodic seed declares one task — `Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.` — and no dossier number anywhere. All three arms cleanly declined to fabricate one. Because control also declined honestly, `scope_did_nothing` is the expected outcome for a non-hallucinating restraint probe (SPEC §15.1 item 9).

### 2. `lt_restraint_01` (Long-Term Restraint)
- **Question**: *"Chức danh của tôi là gì?"*
- **Full Arm Reply**:
  > "Tôi không có thông tin về chức danh của bạn trong ngữ cảnh hiện tại. Vui lòng cung cấp thêm thông tin hoặc tài liệu liên quan để tôi có thể hỗ trợ chính xác hơn."
- **Ablated Arm Reply**:
  > "Tôi không có thông tin về chức danh của bạn trong ngữ cảnh hiện tại. Vui lòng cung cấp thêm thông tin hoặc kiểm tra hồ sơ nhân viên của bạn."
- **Control Arm Reply**:
  > "Tôi không có thông tin về chức danh của bạn trong ngữ cảnh hiện tại. Vui lòng cung cấp thêm thông tin hoặc kiểm tra hồ sơ nhân sự của bạn."
- **Evaluation**: **Valid Refusal in this run, and only in this run.** The long-term profile carries `assistant_persona = điều phối viên vận hành` and no user job title. Here the model declined on all three arms. In run 2 the same probe, same arm, answered *"Chức danh của bạn là điều phối viên vận hành."* and was graded `invented` — see §7. Read this row as one sample of a probe that is not yet independent of its sibling, not as a property of the product.
- **Note**: this probe's full arm carries the run's only seed failure (§3). The gap is episodic and the probe targets `long_term`, whose seeding verified normally, so the verdict stands — but the two facts belong on the same page.

### 3. `sem_restraint_01` (Semantic Restraint)
- **Question**: *"Chính sách công ty nói gì về chế độ nghỉ dài hạn sabbatical?"*
- **Full Arm Reply**:
  > "Trong các tài liệu chính sách hiện có (Chính sách nghỉ phép năm và Chính sách làm thêm giờ) không có quy định nào về chế độ nghỉ dài hạn sabbatical. Hiện chưa có thông tin về chính sách này."
- **Ablated Arm Reply**:
  > "Hiện tại tôi chưa có dữ liệu từ chính sách công ty về chế độ nghỉ dài hạn sabbatical. Bạn có thể cung cấp tài liệu chính sách hoặc cho tôi biết tên văn bản để tôi tra cứu giúp bạn không?"
- **Control Arm Reply**:
  > "Hiện tại tôi không tìm thấy thông tin nào trong cơ sở dữ liệu công ty về chính sách nghỉ dài hạn sabbatical. Bạn có thể tham khảo trực tiếp bộ phận Nhân sự (HR)..."
- **Evaluation**: **Valid Grounded Refusal**. In the full arm, the model demonstrated grounded retrieval: it acknowledged reading the retrieved documents (leave and overtime policies) and stated that sabbatical leave was absent from them.

---

## 6. What This Run Supports and Does Not Support

### Supported Claims
1. **Short-Term Memory**:
   - Both basic recall (`st_recall_01`) and latest state update / override (`st_update_01`) reached `scope_earned_it`: the answer was present with memory and absent on both the ablated and the empty-store arms.
2. **Long-Term Memory**:
   - Persona recall (`lt_recall_01`) earned its answer strictly when memory was present (`scope_earned_it`).
3. **Episodic Memory**:
   - Cross-turn task recall (`ep_recall_01`) earned its answer strictly when memory was present (`scope_earned_it`). This is the probe that was `broken` before the episodic-lookup fix recorded in SPEC §7.5.
4. **Anti-Hallucination Restraint**:
   - All three restraint probes declined on all three arms **in this run**. This does not generalise: the same probe set produced one `invented` restraint answer in run 2 (§7). The correct summary is "three of three declined here; across both runs, two of two on the episodic and semantic restraint probes, one of two on the long-term one".

### Limitations & Caveats
1. **Semantic Recall Inconclusive in this Sample**:
   - `sem_recall_01` produced `unreadable` because the full arm returned no text at all. The recorded stream error is `chat_provider_unavailable: Dịch vụ sinh câu trả lời hiện không khả dụng.` — which is the wrapper's own message, not the underlying fault. `ChatReplyUnavailable` is raised `from` the real error and the detail file persists no `__cause__`, so a rate limit, an auth failure, a timeout and a provider 500 are indistinguishable from what was saved. **The cause of this failure is not recorded and should not be asserted.** What can be said: the full arm produced nothing, while the ablated and control arms both answered normally and missed, so the run does not implicate retrieval or ranking. Run 2 passed this probe cleanly (§7).
2. **One run is one sample** (SPEC §7.3). This is no longer the only run: see §7.
3. **This report quotes model replies in a committed path.** `baselines/` is
   metadata-only by test and `runs/` is gitignored, precisely so that committing
   does not publish model output. This file is a third location that does
   neither. Raised as a decision in §8, not resolved here.

---

## 7. The Paired Reading: Run 2 and Run 3

SPEC §7.3 asks for two runs before a difference is read as anything. Two exist.

| | Run 2 | Run 3 (this report) |
|---|---|---|
| Baseline | `baselines/vi-postgres-2.json` | `baselines/vi-postgres-3.json` |
| Detail | `runs/2026-08-19T16-44-12Z-…` | `runs/2026-08-19T16-46-38Z-…` |
| `ran_at` | `16:35:39Z` | `16:40:42Z` |
| Run key | `4858eff2e91b` | `4858eff2e91b` |

They disagree on exactly **2 of 8** rows, in opposite directions:

| probe | run 2 | run 3 |
|---|---|---|
| `lt_restraint_01` | `unreadable` — full arm `invented`, other two arms empty | `scope_did_nothing` — all three declined |
| `sem_recall_01` | `scope_earned_it` | `unreadable` — full arm returned nothing |
| the other six | identical | identical |

Two of eight is the exact magnitude SPEC §7.3 records as ordinary run-to-run
variance — "about the same size as the difference between two different
databases". So the pair is consistent with the harness behaving as documented,
and neither run on its own supports a claim about the other.

What the pair does establish that neither run does alone:

- **The four `scope_earned_it` recall rows are stable.** `st_recall_01`,
  `st_update_01`, `lt_recall_01` and `ep_recall_01` earned their answers in both
  runs. `ep_recall_01` in particular was `broken` before the episodic-lookup fix
  and is now `scope_earned_it` twice.
- **`sem_recall_01` works, and its flakiness is at the provider rather than the
  retrieval.** Run 2 answered `OT-114` correctly on the full arm and missed on
  both others. Run 3 produced no text on the full arm. Same store, same corpus,
  same query path.
- **`lt_restraint_01` is not yet a sound probe.** In run 2 the full arm answered
  *"Chức danh của bạn là điều phối viên vận hành."* — graded `invented`, with the
  reason *"answered a question it was never given the answer to"*. That string is
  the seeded `assistant_persona`, which the probe set writes so that
  `lt_recall_01` has a fact nobody could guess. The restraint probe asks for the
  *user's* job title; the store holds a plausible-looking answer to it. The probe
  is therefore measuring whether the model conflates two fields, which is a real
  thing to measure but is not what the probe claims to measure. Open; see §8.

Both runs also carried seed failures, and they are different failures: run 2 lost
the episodic seed on two ablated arms with an explicit
`chat_provider_unavailable` cause; run 3 lost it on one full arm with no cause at
all (§3).

---

## 8. Open Items

None of these are decided here.

1. **The `lt_restraint_01` sibling collision.** Leave it — it caught a genuine
   field conflation and that is worth knowing — or re-seed a persona that cannot
   be read as a job title, which makes the probe measure what it says it measures
   and invalidates both baselines as a comparison set.
2. **The unexplained run-3 seed failure** (§3): a seeding turn that reported
   writing and approving an episode, followed by a listing that found none. Not
   reproduced, not diagnosed.
3. **Concurrent runs share a namespace** (§3). `run_key` has no wall-clock
   component, so two runs of the same probe set and model collide completely, and
   the earlier one's teardown deletes the later one's stores. No guard exists.
4. **Reply text in a committed path** (§6, caveat 3).

---

## 9. What This Revision Changed

| # | Section | Was | Now |
|---|---|---|---|
| 1 | §1 | Embedder `gemini-embedding-001` | `gemini-embedding-2`, the configured model; the two take different code paths |
| 2 | §3 | "the episodic scope seed step recorded 0 rows" | The seed reported success; the later storage listing found nothing — a different failure, unique to this run |
| 3 | §6 | `sem_recall_01` failed on "an upstream OpenRouter network/rate limit error", "an external transport failure" | The cause is not recorded and is not asserted; what the arms do and do not implicate is stated instead |
| 4 | §6 | "All 3 restraint probes demonstrated zero invented facts across all arms" | True of this run; false across the pair — run 2 produced one `invented` restraint answer |
| 5 | §7 | (absent) | The paired reading of runs 2 and 3, which is what SPEC §7.3 asks for |
| 6 | §1 | Run Key presented as an identity field | Carries the caveat from SPEC §15.1 item 8: the same key is on all four runs, including one that asked different questions |
| 7 | §4 | Worst-first table with no gloss | Explains why three correct restraint rows sit at the top (SPEC §15.1 item 9) |
| 8 | §5.2 | Silent on the seed failure landing on this probe and arm | Stated, with why the verdict survives it |
| 9 | §1 | "24 calls" | 24 probe asks; ~52 model calls including seeding |

Verified unchanged during the review: every outcome and latency in §4 against the
detail file, all nine quotations in §5 verbatim, the CCCD seed claim in §5.1
against `probes/v1-four-scopes.json`, the baseline-to-detail pairing in §1, and
the offline tier in §2 by re-running it.
