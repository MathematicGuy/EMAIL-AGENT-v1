# Memory Evaluation — Probe Set v3 (`v3_four_scopes_hard`)

**Status:** Proposed.
**Area:** `evaluations/MEMORIES/probes/`, `tests/fixtures/memory_eval/`
**Parent:** [SPEC-memory-evaluation.md](SPEC-memory-evaluation.md) — the harness.
**Harness v3:** [SPEC-memory-eval-harness-v3-scalability.md](SPEC-memory-eval-harness-v3-scalability.md)
**Predecessor dataset:** [SPEC-memory-eval-probe-set-v2.md](SPEC-memory-eval-probe-set-v2.md)

This spec covers **data only, plus the offline guards that keep the data
honest**. No production prompt or retrieval changes. No harness schema bump:
`v3` loads under `schema_version 2.0.0` exactly as `v1` and `v2` do.

Do **not** amend parent SPEC-memory-evaluation.md §3.

---

## 1. Why a third probe set

`v2_four_scopes_wide` is 20 probes on the same four-scope grid. It made n=1
cells readable. After several live runs it is no longer a stress test: many
cells ask whether *anything* came back, and after harness v3 moved episodic
seeds into `{session_id}-seed`, v2's four `short_term` lines sit in a roomier
window than the v2 spec assumed.

v3 keeps **the same 20 cells** and makes them harder so the *new* harness can
be diagnosed:

- crowded `short_term` (confusable office and person; oldest line near the trim)
- same-shape episodic distractor (two CCCD tasks, different offices)
- semantic lookalike form code
- `invented_any` on every near-miss that has a **seeded neighbour**

v3 is **not** a 50-probe volume soak. That is a later set. Do not use the
reserved test-only id `v3_50_probes`.

v1 and v2 stay committed, loadable, and runnable. Every existing baseline
keeps its referent via `probe_set_id` + `probe_set_sha256`.

---

## 2. Identity — a new file, never an edit of v1 or v2

New file `evaluations/MEMORIES/probes/v3-four-scopes-hard.json`, with
`probe_set_id: "v3_four_scopes_hard"`.

Editing `v2-four-scopes-wide.json` in place was rejected for the same reason
v2 did not edit v1: `run_key` hashes `(probe_set_id, model, seed)` and question
text is hashed into nothing. A new id makes the discontinuity a fact in the
report.

The corpus is forked for the same reason: v3 indexes
`tests/fixtures/memory_eval/corpus-v3/`, leaving `corpus/` and `corpus-v2/`
byte-identical so v1 and v2 keep reproducing.

Filename prefix `v3-` is load-bearing. `evaluate_memory.py` with no
`--probe-set` calls `resolve_latest_probe_set` (maximum integer after a leading
`v`). Shipping this file **changes the default launch** from v2 to v3. The
report builder still binds by id + hash, so a v2 baseline cannot silently
grade against v3.

---

## 3. Language — Vietnamese only

The **dataset** is Vietnamese only.

| field | language |
|---|---|
| `label` | Vietnamese |
| `seed.short_term` lines | accented Vietnamese |
| `seed.episodic[].request` | accented Vietnamese |
| `seed.long_term` values | as v2 (`vi`, `Asia/Ho_Chi_Minh`, `trợ lý biệt danh Hải Âu`, `ngắn gọn`) |
| every `question` | accented Vietnamese |
| `expect_any`, `stale_any`, `refusal_about`, `invented_any` | accented Vietnamese (or digits / form codes that appear in the Vietnamese corpus) |
| corpus-v3 body text | accented Vietnamese, plus the existing disclaimer line |

JSON `note` fields stay English. They are operator documentation, not model
input, same as v2.

Unguessable **toponym aliases** in `expect_any` (`Đà Nẵng` / `Da Nang`,
`Cần Thơ` / `Can Tho`) are allowed: they are the same Vietnamese name with
diacritics stripped, not English sentences. Do not add English glosses
(`Wednesday`, `passport`, `form`).

`casefold()` does not strip accents, so `khong ro` never matches `không rõ`
(parent §2.2). Write the accented form the product actually emits.

---

## 4. Probe budget

20 probes. Same grid as v2. Hardness is in the seed, not the count.

| scope | recall | update | restraint | total | seed cost |
|---|---|---|---|---|---|
| `short_term` | 2 | 1 | 2 | **5** | 6 turns |
| `long_term` | 1 | 0 | 3 | **4** | 0 turns (one gateway write) |
| `episodic` | 2 | 1 | 2 | **5** | 4 turns (foreign session) |
| `semantic` | 3 | 0 | 3 | **6** | 0 turns (index build) |
| | | | | **20** | **10 LLM seed turns per seeded arm, plus isolation tax** |

Live cost, harness v3 arithmetic (`S=6`, `E=4`, `N=20`, `N_st=5`):

```text
seed_llm_turns = 2 × (S × N_st + E × N) = 2 × (30 + 80) = 220
ask_turns      = 3 × N                   = 60
total                                    ≈ 280
```

v2 was ≈ 220. The only volume bump is **one extra episode** (`2 × E × N`).
One run at a time remains the operating rule (parent §15.1 item 10).

### 4.1 Same long_term and update ceilings as v2

- **One** `long_term` recall: `assistant_persona` = `Hải Âu`. Language and
  timezone are proven guessable. `response_tone` would contaminate every other
  probe's wording. No `long_term` update (`write_profile` overwrites; parent
  §15.1 item 15).
- **`update` only on `short_term` and `episodic`.** Semantic “update” that is
  actually ranking is declined.

### 4.2 Prompt window (harness v3)

Episodic seeds live in `{session_id}-seed`. The probing session for a
`short_term` probe holds only ST seed turns plus the ask:

> **`len(seed.short_term) + 1 ≤ _MAX_ACTIVE_SESSION_TURNS`**

v3 sits at **7 of 8** (6 ST lines + 1 probe). Overflow still evicts the
**oldest** ST line (the CCCD / Đà Nẵng fact) and reports it as amnesia.

`test_the_seed_fits_the_prompt_window` already uses this formula. Do not
reinstate v2's `len(ST)+len(EP)+1` bound — that would forbid the distractor
episode even though it no longer occupies the window.

CONTROL is never seeded. FULL and ABLATED still write the full `SeedSpec`
(LT + foreign EP + ST-when-targeted). Do not skip `seed_long_term` or
`seed_episodic` on those arms.

---

## 5. Why these 20 cells are harder than v2

Same ids. Difficulty is crowded neighbours, not new rows.

1. **`short_term` is crowded on purpose.** After harness v3, v2's four ST lines
   got easier (EP no longer shares the buffer). v3 uses that room: six lines;
   oldest fact at 7/8 of the trim; two offices (Đà Nẵng vs Hải Phòng) and two
   people (Lê Thu Vân vs Mai Liên). `st_recall_01` / `st_recall_02` fail if the
   model grabs the other name or office. `st_restraint_02` fails if it slots a
   real seeded name into “người nhận”.
2. **Episodic ranking is same-shape.** v2 CCCD vs hộ chiếu is an easy retrieve.
   v3 adds a second **gia hạn CCCD** (Hải Phòng, no assignee). `ep_recall_01`
   can now return the wrong CCCD. Phạm Quốc Huy is assigned on the **passport**
   episode, so naming him on a CCCD-assignee question is a cross-task
   near-miss — not a true recall. Putting the assignee on Hải Phòng CCCD was
   rejected: the restraint question does not name Đà Nẵng, and a true retrieve
   of that episode would then be graded `invented`.
3. **Semantic lookalike.** v2 form codes were unique. v3 adds `OT-141` on a
   night-shift document so `sem_recall_01` can cite the wrong code and
   `sem_restraint_03` can recycle a real neighbouring code.
4. **`invented_any` on every near-miss that has a seeded neighbour.** A fluent
   Vietnamese refusal that still names Lê Thu Vân / Phạm Quốc Huy / `OT-114` /
   `450.000` grades as invention. That is the harness-v3 grader under load.
5. **What we did not do.** No extra probes, no filling restraint holes, no LT
   update, no English questions (CONTROL must not pass from English priors).
   ABLATED vs CONTROL still separates leak from `scope_did_nothing`.

---

## 6. The seed

```json
"seed": {
  "short_term": [
    "Tôi đang xử lý yêu cầu gia hạn CCCD cho văn phòng Đà Nẵng.",
    "Hồ sơ này do chị Lê Thu Vân ký duyệt.",
    "Hạn chót của việc đó là thứ Ba.",
    "Đính chính: hạn chót đã dời sang thứ Tư.",
    "Văn phòng Hải Phòng đang chờ hồ sơ liên quan.",
    "Chị Mai Liên vừa gửi email nhắc hạn nộp."
  ],
  "long_term": {
    "language": "vi",
    "timezone": "Asia/Ho_Chi_Minh",
    "assistant_persona": "trợ lý biệt danh Hải Âu",
    "response_tone": "ngắn gọn"
  },
  "episodic": [
    { "request": "Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.", "approve": true },
    { "request": "Tạo một tác vụ cấp lại hộ chiếu cho văn phòng Cần Thơ, nộp hồ sơ ngày 5 tháng 9, giao cho anh Phạm Quốc Huy.", "approve": true },
    { "request": "Tạo một tác vụ dời ngày nộp hồ sơ hộ chiếu Cần Thơ sang ngày 12 tháng 9.", "approve": true },
    { "request": "Tạo một tác vụ gia hạn CCCD cho văn phòng Hải Phòng.", "approve": true }
  ],
  "semantic": { "corpus_dir": "tests/fixtures/memory_eval/corpus-v3" }
}
```

Load-bearing choices:

- **Line 1 is still the CCCD / Đà Nẵng fact.** It is the first to be evicted if
  the window overflows. `st_recall_01` is therefore also a window-stress probe.
- **Hải Phòng in ST is not a second CCCD renewal.** “đang chờ hồ sơ liên quan”
  must not say `yêu cầu gia hạn CCCD cho văn phòng Hải Phòng`, or
  `st_recall_01` has two true answers.
- **Mai Liên is not the signer and not the recipient.** She sent a reminder.
  Do not write `ký duyệt` or `người nhận` on that line.
- **The Hải Phòng episode is the ranking distractor.** Same task shape as Đà
  Nẵng CCCD, different office, **no assignee**. Both CCCD episodes are
  approved, so both are retrievable. Phạm Quốc Huy is named only on the
  passport-create episode, so he is a neighbour the CCCD-assignee probe can
  steal, not a true answer to it.
- **Cần Thơ 5 tháng 9 → 12 tháng 9 stays the supersession pair.** Both
  approved. Answering 5 tháng 9 is a confident wrong date, not a gap.
- Every `episodic` request keeps the `Tạo một tác vụ` shape that
  `is_explicit_task_request` accepts. The existing offline test will assert
  this for v3 as well.
- **`long_term` is unchanged from v2.** The persona stays a bird nickname
  (parent §15.1 item 12).

### 6.1 Restraint holes — do not fill them

These facts must **not** appear in seed or corpus. Filling any of them silently
turns a restraint probe into a recall probe.

| hole | owned by |
|---|---|
| mã số / số hiệu of the CCCD renewal request | `st_restraint_01` |
| tên người nhận hồ sơ at Đà Nẵng | `st_restraint_02` |
| chức danh / chức vụ of the user | `lt_restraint_01` |
| số điện thoại of the user | `lt_restraint_02` |
| khung giờ làm việc of the user | `lt_restraint_03` |
| số hồ sơ / mã hồ sơ of the Đà Nẵng CCCD task | `ep_restraint_01` |
| người được giao of the Đà Nẵng CCCD task | `ep_restraint_02` |
| sabbatical / nghỉ dài hạn policy | `sem_restraint_01` |
| công tác phí nước ngoài | `sem_restraint_02` |
| form code on the equipment / laptop document | `sem_restraint_03` |

---

## 7. The probes

20 probes, same ids as v2. All questions Vietnamese and accented. `episodic`
questions carry `_EPISODIC_CUES` (`tác vụ trước`). `semantic` questions carry
`_SEMANTIC_CUES` (`chính sách công ty`). No question may parse as an explicit
task request.

`label`:

> Bốn phạm vi bộ nhớ, bản khó: 20 câu hỏi, cùng lưới với v2, seed dày hơn và mã biểu mẫu gần giống

### 7.1 `short_term` — 5

| id | test | question | expects |
|---|---|---|---|
| `st_recall_01` | recall | Yêu cầu gia hạn đó là cho văn phòng nào? | `Đà Nẵng`, `Da Nang` |
| `st_recall_02` | recall | Hồ sơ này do ai ký duyệt? | `Lê Thu Vân`, `Thu Vân` |
| `st_update_01` | update | Hạn chót của yêu cầu gia hạn CCCD là khi nào? | `thứ Tư`, `thứ 4`; stale `thứ Ba`, `thứ 3` |
| `st_restraint_01` | restraint | Mã số của yêu cầu gia hạn CCCD này là gì? | refusal; `refusal_about`: `mã số`, `số hiệu` |
| `st_restraint_02` | restraint | Người nhận hồ sơ ở văn phòng Đà Nẵng tên là gì? | refusal; `refusal_about`: `người nhận hồ sơ`, `tên người nhận`; `invented_any`: `Lê Thu Vân`, `Thu Vân`, `Mai Liên` |

### 7.2 `long_term` — 4

| id | test | question | expects |
|---|---|---|---|
| `lt_recall_01` | recall | Tôi đã đặt bạn ở vai trò nào khi trả lời tôi? | `Hải Âu` |
| `lt_restraint_01` | restraint | Chức danh của tôi là gì? | refusal; `refusal_about`: `chức danh`, `chức vụ` |
| `lt_restraint_02` | restraint | Số điện thoại liên hệ của tôi là số nào? | refusal; `refusal_about`: `số điện thoại`, `số liên hệ` |
| `lt_restraint_03` | restraint | Tôi thường làm việc trong khung giờ nào? | refusal; `refusal_about`: `khung giờ làm việc`, `giờ làm việc` |

`lt_restraint_03` is still the timezone near-miss. It has **no** `invented_any`:
the tempting invention is a plausible work schedule, and no work schedule is in
the seed. Listing `8h-17h` would fail the grounding test in §8.2. Listing the
timezone string would punish a reply that honestly names the one time fact it
was given. Refusal-only grading stands.

`lt_restraint_01` keeps v2's known pronoun ambiguity (`tôi`). Leave it.

### 7.3 `episodic` — 5

All five carry `tác vụ trước`. Probe sessions are fresh; ST is not in the
buffer. Ranking must separate two CCCD episodes.

| id | test | question | expects |
|---|---|---|---|
| `ep_recall_01` | recall | Tác vụ trước về gia hạn CCCD là cho văn phòng nào? | `Đà Nẵng`, `Da Nang` — Hải Phòng is a ranking miss |
| `ep_recall_02` | recall | Tác vụ trước về cấp lại hộ chiếu là cho văn phòng nào? | `Cần Thơ`, `Can Tho` |
| `ep_update_01` | update | Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là ngày nào? | `12 tháng 9`, `12/9`; stale `5 tháng 9`, `5/9` |
| `ep_restraint_01` | restraint | Số hồ sơ trên tác vụ trước về gia hạn CCCD là bao nhiêu? | refusal; `refusal_about`: `số hồ sơ`, `mã hồ sơ` |
| `ep_restraint_02` | restraint | Ai được giao xử lý tác vụ trước về gia hạn CCCD? | refusal; `refusal_about`: `người phụ trách`, `người được giao`; `invented_any`: `Phạm Quốc Huy`, `Quốc Huy` |

Neither CCCD episode names an assignee. Inventing Phạm Quốc Huy (the
passport task's assignee) is the failure. A decline about the previous CCCD
task without naming a person is a pass.

### 7.4 `semantic` — 6

All six carry `chính sách công ty`. All require `CHAT_COMPANY_RAG_ENABLED=true`.

| id | test | question | expects |
|---|---|---|---|
| `sem_recall_01` | recall | Chính sách công ty yêu cầu nộp đề nghị làm thêm giờ qua biểu mẫu nào? | `OT-114`, `OT 114` — `OT-141` is the lookalike miss |
| `sem_recall_02` | recall | Chính sách công ty yêu cầu đăng ký làm việc từ xa qua biểu mẫu nào? | `WFH-207`, `WFH 207` |
| `sem_recall_03` | recall | Chính sách công ty quy định công tác phí trong nước mỗi ngày là bao nhiêu? | `450.000`, `450000`, `450 000` |
| `sem_restraint_01` | restraint | Chính sách công ty nói gì về chế độ nghỉ dài hạn sabbatical? | refusal; nouns name nghỉ dài hạn / sabbatical, **not** bare `chính sách` |
| `sem_restraint_02` | restraint | Chính sách công ty quy định công tác phí cho chuyến đi nước ngoài là bao nhiêu? | refusal; `invented_any`: `450.000` |
| `sem_restraint_03` | restraint | Chính sách công ty yêu cầu nộp đề nghị đổi laptop hỏng qua biểu mẫu nào? | refusal; `invented_any`: `OT-114`, `WFH-207`, `OT-141` |

`sem_restraint_01` `refusal_about` (copy v2, Vietnamese):

- `chính sách nghỉ dài hạn`
- `chế độ nghỉ dài hạn`
- `chính sách sabbatical`
- `quy định về sabbatical`

`sem_restraint_02` `refusal_about`:

- `công tác phí nước ngoài`
- `công tác phí cho chuyến đi nước ngoài`
- `mức công tác phí quốc tế`

`sem_restraint_03` `refusal_about`:

- `biểu mẫu đổi laptop`
- `biểu mẫu đổi thiết bị`
- `mẫu đề nghị đổi máy`

`invented_any` for `sem_restraint_02` lists only the verbatim corpus form
`450.000`. Digit-collapsed aliases belong in `expect_any` on the recall
sibling, not here: the grounding test is a substring of seed material.

---

## 8. The corpus

`tests/fixtures/memory_eval/corpus-v3/` — six short Vietnamese documents. All
invented text; each carries the existing “không phải chính sách thật”
disclaimer line, per `evaluations/HARNESS-GUIDE.md` §3.

| file | carries | exists for |
|---|---|---|
| `leave-policy.md` | annual leave, 5 working days notice | `sem_restraint_01`'s near neighbour |
| `overtime-policy.md` | **form `OT-114`** for làm thêm giờ | `sem_recall_01` |
| `overtime-night-policy.md` | **form `OT-141`** for **làm ca đêm**, not làm thêm giờ | lookalike trap; `invented_any` on `sem_restraint_03` |
| `remote-work-policy.md` | **form `WFH-207`** | `sem_recall_02` |
| `travel-expense-policy.md` | **domestic per-diem `450.000 đồng`**, domestic only | `sem_recall_03`, `sem_restraint_02` |
| `equipment-policy.md` | laptop replacement, **deliberately no form code** | `sem_restraint_03` |

Copy `leave-policy.md`, `overtime-policy.md`, `remote-work-policy.md`,
`travel-expense-policy.md`, and `equipment-policy.md` from `corpus-v2/`
**unchanged**. The new file is the only delta.

`overtime-night-policy.md` must:

- be Vietnamese
- name **làm ca đêm** (night shift), never **làm thêm giờ** (or
  `sem_recall_01` has two true codes)
- contain the lookalike code `OT-141` once
- contain no sabbatical, no overseas per-diem, no laptop form
- end with the disclaimer line

Authoritative body:

```markdown
# Chính sách làm ca đêm

Đăng ký làm ca đêm phải gửi trước ít nhất hai ngày làm việc và được quản lý
trực tiếp xác nhận trên cổng nội bộ.

Mọi đăng ký làm ca đêm phải nộp qua biểu mẫu OT-141 trên cổng nội bộ.

Tài liệu này không áp dụng cho đề nghị làm thêm giờ. Tài liệu này là nội dung
tổng hợp dùng cho đánh giá, không phải chính sách thật.
```

The `450.000` figure, the absence of a code in `equipment-policy.md`, the
absence of an overseas rate, and `OT-114` vs `OT-141` staying on different
documents are load-bearing. Edits that mix them silently change what two
probes measure.

---

## 9. Changes outside the dataset

Three, all offline or operator docs. No production code. No schema bump.

### 9.1 Ground `invented_any` in the seed

New assertion in the parametrized honesty suite (same files that already
discover `probes/*.json`):

Every `invented_any` phrase, `casefold()`, must appear as a substring of
**this set's** seed material — the same concatenation
`test_recall_expectations_exist_somewhere_in_the_seed` already builds
(ST lines + LT values + EP requests + corpus-v3 files).

If a listed invention is not in the seed, it is not a near-miss; it is a
random string, and the probe measures nothing the grader can fairly call
invention-from-neighbour.

v1/v2 already satisfy this for the phrases they declare. The test is
parametrized, so they keep the guard for free.

### 9.2 Operator docs: default launch becomes v3

When the JSON lands, update the probe-set table in
`evaluations/MEMORIES/README.md` (and the one-line example in
`evaluations/MEMORIES/RUNBOOK.md` if it still names v2 as the default file).
`.agents/skills/mem-eval/SKILL.md` already speaks of “latest `vN-*.json`”;
do not rewrite the skill unless a sentence still hardcodes v2 as launch
default.

### 9.3 Parent spec §3 is not in scope

Do not amend SPEC-memory-evaluation.md §3. Parent §15.1 item 16 still
describes the pre-harness-v3 window (`len(ST)+len(EP)+1`); that drift is a
parent-docs debt, not this dataset's job. The honesty test and harness v3
spec are the live rule.

---

## 10. What this does not change

- **No production code** under `src/cowork_agent/` outside `memory_eval` tests.
- **No schema bump.** `schema_version 2.0.0`.
- **No new grade, verdict, or report field.**
- **v1 and v2 keep working.** Files and corpora unmodified.
- **No golden LLM judge** (v2 §8 stays v2-only). Out of scope.
- **No volume set.** Not 32–50 probes. Not `v3_50_probes`.
- **CI still does not gate on live results** (parent §13). Offline guards
  block; the live run measures.
- **Embeddings stay Gemini** for eval. Do not switch
  `DOCUMENT_EMBEDDING_PROVIDER` for the verify run.

---

## 11. Commands

Honesty (offline; required to accept the JSON):

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run pytest tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py -q
uv run python scripts/evaluate_memory.py --probe-set evaluations/MEMORIES/probes/v3-four-scopes-hard.json --dry-run
```

Default launch must resolve to the v3 file:

```powershell
uv run python -c "from scripts.evaluate_memory import resolve_latest_probe_set; print(resolve_latest_probe_set())"
```

Expected: `evaluations\MEMORIES\probes\v3-four-scopes-hard.json`

Live harness verify (after honesty is green; `/mem-eval` skill; **not** a
product pass-rate gate):

```powershell
uv run python scripts/memeval_preflight.py
uv run python scripts/evaluate_memory.py --output evaluations/MEMORIES/baselines/v3-four-scopes-hard-sqlite.json
uv run python scripts/build_memory_evaluation_report.py --baseline evaluations/MEMORIES/baselines/v3-four-scopes-hard-sqlite.json
```

---

## 12. Boundaries

- **Always:** Vietnamese-only dataset fields listed in §3; new file + new
  `probe_set_id`; CONTROL never seeded; FULL/ABLATED write LT + foreign EP;
  parametrize honesty over every `probes/*.json`; `uv run`.
- **Ask first:** SQL migrations, parent SPEC-memory-evaluation.md edits,
  production prompt / retrieval changes, adding a volume (v4) set, Jina
  embeddings for eval.
- **Never:** edit v1/v2 JSON or `corpus/` / `corpus-v2/`; commit `.env` or
  secrets; fill a §6.1 restraint hole; use `probe_set_id` `v3_50_probes`;
  skip `seed_long_term` / `seed_episodic` on FULL/ABLATED; treat a low Full-arm
  pass rate as a failed dataset.

---

## 13. Verification

| Check | How |
|---|---|
| v3 loads | `load_probe_set` via parametrized `test_the_shipped_probe_set_loads` |
| every cue-gated probe fires its read | parametrized `test_cue_gated_probes_actually_fire_their_retrieval` |
| no question reads as a task order | parametrized `test_recall_probes_do_not_themselves_create_tasks` |
| every seed request is an explicit task | parametrized `test_episodic_seed_requests_are_accepted_as_explicit_task_requests` |
| every recall expectation is in this set's seed | parametrized `test_recall_expectations_exist_somewhere_in_the_seed` |
| every restraint declares `refusal_about` | parametrized existing test |
| the seed fits the ST window | existing `len(ST)+1` test; v3 uses 7 of 8 |
| every `invented_any` is in this set's seed | **new** parametrized test, §9.1 |
| dry-run wiring | `--probe-set v3-four-scopes-hard.json --dry-run` |
| default launch is v3 | `resolve_latest_probe_set()` returns that path |
| live harness verify | `/mem-eval`: preflight, evaluate, report binds `v3_four_scopes_hard` + sha256 |

### 13.1 What no offline check can establish

Whether an expectation is **guessable**. The never-filled (`control`) arm is
the instrument. Expect the first live run to retire one or two expectations
on that evidence — parent §7.4 is a record of that happening to v1.

### 13.2 What the live `/mem-eval` run is for

**Harness verification, not product greenwash.** Success means:

- preflight exit 0
- launch without `--probe-set` loads v3 (or an explicit `--probe-set` to the
  v3 file if another `vN` appears)
- baseline JSON has `probe_set_id: "v3_four_scopes_hard"` and a sha256 of
  that file
- report binds that id + hash (must not bind a v2 baseline)
- 3-arm matrix is present; if `"aborted": true`, still run the report
- `invented_any` and foreign-EP distractor effects are visible in detail
  rows (invention grades, ranking misses), not silently dropped

A low Full-arm pass rate is **compatible with success** if hardness worked.
Do not “fix” the dataset to raise the score. Do not change production
prompts from this run unless triage names Concern D with a failing test
first (`mem-eval` skill §3).

---

## 14. Success criteria

1. `evaluations/MEMORIES/probes/v3-four-scopes-hard.json` exists with
   `probe_set_id` `v3_four_scopes_hard`, `schema_version` `2.0.0`, the §6
   seed, and the 20 probes in §7.
2. `tests/fixtures/memory_eval/corpus-v3/` exists as specified in §8.
3. v1, v2, `corpus/`, and `corpus-v2/` are byte-identical to before this
   change.
4. Honesty tests in §13 are green, including the new `invented_any` grounding
   test.
5. Default launch resolves to the v3 file.
6. One live `/mem-eval` run against v3 completes the §13.2 checklist.

---

## 15. Open questions

None. Volume (~32–50) is explicitly a later probe set, not an unresolved
choice in this spec.
