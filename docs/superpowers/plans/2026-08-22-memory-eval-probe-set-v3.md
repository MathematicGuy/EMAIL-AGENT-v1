# Memory Eval Probe Set v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Controller and every subagent: also load `using-agent-skills`. For this dataset work that means `test-driven-development` on Task 1, `verification-before-completion` before any “done” claim, and `using-git-worktrees` before the first edit. After the JSON lands, Task 6 follows `.agents/skills/mem-eval/SKILL.md`. Before the last review, `code-simplifier` on any Python you added (the JSON/corpus are data — do not “simplify” Vietnamese questions).

**Goal:** Ship the diagnostic v3 probe set (`v3_four_scopes_hard`, 20 harder-than-v2 cells, Vietnamese only) plus the `invented_any` grounding test, then verify the harness with `/mem-eval`.

**Architecture:** Data-only change. Honesty tests already discover `evaluations/MEMORIES/probes/*.json`. Add one parametrized guard *before* the JSON so a dead `invented_any` cannot ship. Fork `corpus-v3/` from v2; add one night-shift lookalike doc. New probe file with a new `probe_set_id`. Default CLI launch becomes v3 via integer prefix; report still binds by id + sha256. No production prompts, no schema bump, no parent SPEC §3 edit.

**Tech Stack:** Existing memory-eval harness (`schema_version 2.0.0`), pytest honesty suite, `uv run`, PowerShell on Windows.

**Spec:** `tasks/specs/SPEC-memory-eval-probe-set-v3.md` (commit `b4e175b`)
**Design:** `docs/superpowers/specs/2026-08-22-memory-eval-probe-set-v3-design.md`
**Parent:** `tasks/specs/SPEC-memory-evaluation.md` (do not edit §3)
**Harness v3:** `tasks/specs/SPEC-memory-eval-harness-v3-scalability.md`

Also copy this plan, at execution start, to `tasks/plans/PLAN-memory-eval-probe-set-v3.md` and `docs/superpowers/plans/2026-08-22-memory-eval-probe-set-v3.md` so it lives in the repo.

## Global Constraints

- Dataset fields in spec §3 are **Vietnamese only** (questions, seed, corpus, `expect_any` / `stale_any` / `refusal_about` / `invented_any`). JSON `note` fields stay English. Toponym aliases `Da Nang` / `Can Tho` allowed; no English glosses.
- New file `evaluations/MEMORIES/probes/v3-four-scopes-hard.json`, `probe_set_id` exactly `v3_four_scopes_hard`, `schema_version` exactly `2.0.0`.
- Never edit `v1-four-scopes.json`, `v2-four-scopes-wide.json`, `tests/fixtures/memory_eval/corpus/`, or `corpus-v2/`.
- Never use `probe_set_id` `v3_50_probes` (test-only reserved id).
- Never fill a spec §6.1 restraint hole (request id, Đà Nẵng recipient, job title, phone, work hours, CCCD case number, CCCD assignee, sabbatical, overseas per-diem, laptop form code).
- CONTROL never seeded. FULL/ABLATED still write LT + foreign EP. Do not skip `seed_long_term` / `seed_episodic`.
- Window: `len(seed.short_term) + 1 <= _MAX_ACTIVE_SESSION_TURNS` (already imported, not the literal 8). v3 is 6 ST + 1 ask = 7.
- No production code under `src/cowork_agent/` except `memory_eval` **tests**. No schema bump. No golden LLM judge. No volume (~32–50) set.
- Always `uv run`. PowerShell: no `&&`; `git commit -F <file>`; do not `git commit -m` with here-strings.
- Work in a git worktree on `feat/memory-eval-probe-set-v3` branched from current `dev` (includes spec commit `b4e175b`). Do not implement on root `dev`.
- Live `/mem-eval` is harness verification, not a product pass-rate gate. Do not “fix” questions to raise Full-arm score. Do not switch `DOCUMENT_EMBEDDING_PROVIDER` to Jina.
- Test route: `tests/unit/features/ai_chat/memory_eval/` (R2) plus `tests/unit/scripts/test_evaluate_memory.py` if you touch resolve-latest. Read `tests/README.md` §3 before adding a test — `invented_any` grounding belongs next to `test_recall_expectations_exist_somewhere_in_the_seed`, not a new file.

## File map

| Path | Responsibility |
|---|---|
| `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py` | New parametrized `invented_any` grounding test (same seed concatenation as recall expectations) |
| `tests/fixtures/memory_eval/corpus-v3/*.md` | Fork of corpus-v2 + `overtime-night-policy.md` (`OT-141`) |
| `evaluations/MEMORIES/probes/v3-four-scopes-hard.json` | The dataset |
| `evaluations/MEMORIES/README.md` | Probe-set table + default-launch example |
| `evaluations/MEMORIES/RUNBOOK.md` | Default file name / cost line |
| `tasks/plans/PLAN-memory-eval-probe-set-v3.md` | Repo copy of this plan |

Do **not** touch: `src/cowork_agent/**` production, parent SPEC §3, v1/v2 JSON, corpus-v1/v2, leaderboard as a ship gate, golden prompts.

---

### Task 1: Ground `invented_any` in the seed (red first)

**Files:**
- Modify: `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py` (append after `test_recall_expectations_exist_somewhere_in_the_seed`, currently ends ~line 132)
- Test: same file

**Interfaces:**
- Consumes: `probe_set_path` fixture from `conftest.py` (`PROBE_SET_PATHS = tuple(sorted(PROBE_SET_DIR.glob("*.json")))`); `_load(path)`; `REPO_ROOT`
- Produces: `test_invented_any_phrases_exist_somewhere_in_the_seed(probe_set_path: Path) -> None` — parametrized over every committed probe set

Guards land **before** the data they guard. v1 has no `invented_any` (vacuous pass). v2 lists `Lê Thu Vân` / `Thu Vân`, both in its ST seed (pass). A later v3 phrase that is not in corpus-v3 / seed must go red.

- [ ] **Step 1: Write the failing-capable test**

Append this function. Reuse the same `material` concatenation as `test_recall_expectations_exist_somewhere_in_the_seed` — do not invent a second corpus reader.

```python
def test_invented_any_phrases_exist_somewhere_in_the_seed(probe_set_path: Path) -> None:
    # invented_any is a near-miss only if the neighbour was actually stored.
    # A phrase that appears nowhere in this set's seed is a random string, and
    # the grader cannot fairly call it invention-from-neighbour.
    # Same material concatenation as recall-expectation grounding: this set's
    # own corpus_dir, never a sibling set's documents.
    data = _load(probe_set_path)
    seed = data["seed"]
    corpus_dir = REPO_ROOT / seed["semantic"]["corpus_dir"]
    material = "\n".join(
        [
            *seed["short_term"],
            *(str(value) for value in seed["long_term"].values()),
            *(entry["request"] for entry in seed["episodic"]),
            *(path.read_text(encoding="utf-8") for path in sorted(corpus_dir.iterdir())),
        ]
    ).casefold()

    missing: list[str] = []
    for probe in data["probes"]:
        for phrase in probe.get("invented_any") or []:
            if phrase.casefold() not in material:
                missing.append(f"{probe['id']}:{phrase!r}")
    assert not missing, (
        f"{probe_set_path.name} invented_any phrases absent from seed: {missing}"
    )
```

- [ ] **Step 2: Prove it can fail**

In a throwaway local snippet (do not commit), copy v2's JSON into `%TEMP%`, add `"invented_any": ["XYZ-NOT-IN-SEED"]` on `st_restraint_02`, and run the assertion body against that path. Expected: AssertionError naming `XYZ-NOT-IN-SEED`. Delete the scratch file.

- [ ] **Step 3: Run against committed sets**

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run pytest tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py::test_invented_any_phrases_exist_somewhere_in_the_seed tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py::test_recall_expectations_exist_somewhere_in_the_seed -q
```

Expected: PASS (v1 vacuously, v2 because `Lê Thu Vân` is in ST).

- [ ] **Step 4: Commit**

```powershell
git add tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py
git commit -F <tempfile>
```

Message:

```
test(memory-eval): ground invented_any phrases in each probe set seed
```

---

### Task 2: Fork `corpus-v3/`

**Files:**
- Create: `tests/fixtures/memory_eval/corpus-v3/leave-policy.md` (byte-copy from corpus-v2)
- Create: `tests/fixtures/memory_eval/corpus-v3/overtime-policy.md` (byte-copy)
- Create: `tests/fixtures/memory_eval/corpus-v3/remote-work-policy.md` (byte-copy)
- Create: `tests/fixtures/memory_eval/corpus-v3/travel-expense-policy.md` (byte-copy)
- Create: `tests/fixtures/memory_eval/corpus-v3/equipment-policy.md` (byte-copy)
- Create: `tests/fixtures/memory_eval/corpus-v3/overtime-night-policy.md` (new; spec §8 body verbatim)

**Interfaces:**
- Consumes: `tests/fixtures/memory_eval/corpus-v2/*.md`
- Produces: six Vietnamese markdown files; `OT-114` still only in overtime-policy; `OT-141` only in overtime-night-policy; equipment still has no form code

- [ ] **Step 1: Copy the five v2 documents unchanged**

```powershell
New-Item -ItemType Directory -Force tests/fixtures/memory_eval/corpus-v3 | Out-Null
Copy-Item tests/fixtures/memory_eval/corpus-v2/*.md tests/fixtures/memory_eval/corpus-v3/
```

Do not then edit those five copies. Leave `corpus/` and `corpus-v2/` untouched.

- [ ] **Step 2: Write `overtime-night-policy.md` exactly**

```markdown
# Chính sách làm ca đêm

Đăng ký làm ca đêm phải gửi trước ít nhất hai ngày làm việc và được quản lý
trực tiếp xác nhận trên cổng nội bộ.

Mọi đăng ký làm ca đêm phải nộp qua biểu mẫu OT-141 trên cổng nội bộ.

Tài liệu này không áp dụng cho đề nghị làm thêm giờ. Tài liệu này là nội dung
tổng hợp dùng cho đánh giá, không phải chính sách thật.
```

Must say **làm ca đêm**, never **làm thêm giờ** as the thing OT-141 registers (the last paragraph may mention làm thêm giờ only to exclude it). Must contain `OT-141` once. Must end with the disclaimer line.

- [ ] **Step 3: Verify load-bearing strings**

```powershell
uv run python -c @"
from pathlib import Path
root = Path('tests/fixtures/memory_eval/corpus-v3')
text = {p.name: p.read_text(encoding='utf-8') for p in root.glob('*.md')}
joined = '\n'.join(text.values())
assert joined.count('OT-114') == 1
assert joined.count('OT-141') == 1
assert joined.count('WFH-207') == 1
assert joined.count('450.000') == 1
assert 'OT-114' in text['overtime-policy.md']
assert 'OT-141' in text['overtime-night-policy.md']
assert 'OT-141' not in text['overtime-policy.md']
assert 'làm thêm giờ' not in text['overtime-night-policy.md'].split('không áp dụng')[0]
assert 'sabbatical' not in joined.lower() or 'leave-policy' in 'ok'
assert not any(code in text['equipment-policy.md'] for code in ('OT-114','OT-141','WFH-207','biểu mẫu OT','biểu mẫu WFH'))
assert all('không phải chính sách thật' in body for body in text.values())
print('corpus-v3 ok', sorted(text))
"@
```

Expected: `corpus-v3 ok` and six filenames. `equipment-policy.md` must remain free of form codes.

- [ ] **Step 4: Commit**

Message: `test(memory-eval): add corpus-v3 with OT-141 night-shift lookalike`

---

### Task 3: Write `v3-four-scopes-hard.json`

**Files:**
- Create: `evaluations/MEMORIES/probes/v3-four-scopes-hard.json`

**Interfaces:**
- Consumes: spec §6 seed (verbatim) and §7 questions (verbatim); Task 2 corpus path `tests/fixtures/memory_eval/corpus-v3`
- Produces: `ProbeSet` with `probe_set_id="v3_four_scopes_hard"`, 20 probes, loadable by `load_probe_set`

Authoring rules (honesty tests now enforce them):

- Every EP question contains the exact cue `tác vụ trước`. Every SEM question contains `chính sách công ty`.
- No question may parse as `is_explicit_task_request` (no `tạo`/`lập`/`lên` near `tác vụ`).
- Every `expect_refusal` probe declares `refusal_about` naming **its own noun**, never bare `chính sách`.
- `invented_any` only on `st_restraint_02`, `ep_restraint_02`, `sem_restraint_02`, `sem_restraint_03` as listed. `lt_restraint_03` has **no** `invented_any`.
- 6 ST lines, 4 EP entries, window 7 ≤ 8.
- `note` fields English, modeled on v2 (what it catches, why unguessable / why harder).

- [ ] **Step 1: Write the probe file with this exact seed and these exact questions**

Use this JSON. Do not rephrase questions. Do not add a fifth episode. Do not put Phạm Quốc Huy on the Hải Phòng CCCD request.

```json
{
  "schema_version": "2.0.0",
  "probe_set_id": "v3_four_scopes_hard",
  "label": "Bốn phạm vi bộ nhớ, bản khó: 20 câu hỏi, cùng lưới với v2, seed dày hơn và mã biểu mẫu gần giống",
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
      {
        "request": "Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.",
        "approve": true
      },
      {
        "request": "Tạo một tác vụ cấp lại hộ chiếu cho văn phòng Cần Thơ, nộp hồ sơ ngày 5 tháng 9, giao cho anh Phạm Quốc Huy.",
        "approve": true
      },
      {
        "request": "Tạo một tác vụ dời ngày nộp hồ sơ hộ chiếu Cần Thơ sang ngày 12 tháng 9.",
        "approve": true
      },
      {
        "request": "Tạo một tác vụ gia hạn CCCD cho văn phòng Hải Phòng.",
        "approve": true
      }
    ],
    "semantic": {
      "corpus_dir": "tests/fixtures/memory_eval/corpus-v3"
    }
  },
  "probes": [
    {
      "id": "st_recall_01",
      "targets": "short_term",
      "test": "recall",
      "question": "Yêu cầu gia hạn đó là cho văn phòng nào?",
      "expect_any": ["Đà Nẵng", "Da Nang"],
      "note": "Oldest ST line (evicted first if the 8-turn window overflows). Hải Phòng also sits in the buffer as a related-file wait, not as a second CCCD renewal. Answering Hải Phòng is grabbing the neighbour."
    },
    {
      "id": "st_recall_02",
      "targets": "short_term",
      "test": "recall",
      "question": "Hồ sơ này do ai ký duyệt?",
      "expect_any": ["Lê Thu Vân", "Thu Vân"],
      "note": "Signer vs reminder-sender. Mai Liên is also in the buffer and must not be reported as the signer. A made-up name cannot be reconstructed from the question."
    },
    {
      "id": "st_update_01",
      "targets": "short_term",
      "test": "update",
      "question": "Hạn chót của yêu cầu gia hạn CCCD là khi nào?",
      "expect_any": ["thứ Tư", "thứ 4"],
      "stale_any": ["thứ Ba", "thứ 3"],
      "note": "Both days were stated; only the later one is true. Numeric weekday forms are listed because Vietnamese writes both."
    },
    {
      "id": "st_restraint_01",
      "targets": "short_term",
      "test": "restraint",
      "question": "Mã số của yêu cầu gia hạn CCCD này là gì?",
      "expect_refusal": true,
      "refusal_about": ["mã số", "số hiệu"],
      "note": "No request id was seeded. Do not add one. No invented_any: no neighbouring id exists in the seed."
    },
    {
      "id": "st_restraint_02",
      "targets": "short_term",
      "test": "restraint",
      "question": "Người nhận hồ sơ ở văn phòng Đà Nẵng tên là gì?",
      "expect_refusal": true,
      "refusal_about": ["người nhận hồ sơ", "tên người nhận"],
      "invented_any": ["Lê Thu Vân", "Thu Vân", "Mai Liên"],
      "note": "Recipient never named. Two real Vietnamese names sit in the buffer (signer and reminder-sender). Naming either is wrap-invention."
    },
    {
      "id": "lt_recall_01",
      "targets": "long_term",
      "test": "recall",
      "question": "Tôi đã đặt bạn ở vai trò nào khi trả lời tôi?",
      "expect_any": ["Hải Âu"],
      "note": "Unguessable persona nickname. Only long_term recall by design (spec §4.1)."
    },
    {
      "id": "lt_restraint_01",
      "targets": "long_term",
      "test": "restraint",
      "question": "Chức danh của tôi là gì?",
      "expect_refusal": true,
      "refusal_about": ["chức danh", "chức vụ"],
      "note": "Job title never stored. Known pronoun ambiguity on tôi; leave it (v2 inherit)."
    },
    {
      "id": "lt_restraint_02",
      "targets": "long_term",
      "test": "restraint",
      "question": "Số điện thoại liên hệ của tôi là số nào?",
      "expect_refusal": true,
      "refusal_about": ["số điện thoại", "số liên hệ"],
      "note": "No phone in the four profile fields. Inventing a number is the costly failure."
    },
    {
      "id": "lt_restraint_03",
      "targets": "long_term",
      "test": "restraint",
      "question": "Tôi thường làm việc trong khung giờ nào?",
      "expect_refusal": true,
      "refusal_about": ["khung giờ làm việc", "giờ làm việc"],
      "note": "Timezone near-miss. No invented_any: a work schedule is not in the seed, and listing the timezone string would punish an honest time fact."
    },
    {
      "id": "ep_recall_01",
      "targets": "episodic",
      "test": "recall",
      "question": "Tác vụ trước về gia hạn CCCD là cho văn phòng nào?",
      "expect_any": ["Đà Nẵng", "Da Nang"],
      "note": "Two approved CCCD episodes exist (Đà Nẵng and Hải Phòng). Answering Hải Phòng is a ranking miss, not a gap. Cue tác vụ trước required."
    },
    {
      "id": "ep_recall_02",
      "targets": "episodic",
      "test": "recall",
      "question": "Tác vụ trước về cấp lại hộ chiếu là cho văn phòng nào?",
      "expect_any": ["Cần Thơ", "Can Tho"],
      "note": "Different subject from the CCCD pair so a CCCD retrieve here is visible ranking failure."
    },
    {
      "id": "ep_update_01",
      "targets": "episodic",
      "test": "update",
      "question": "Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là ngày nào?",
      "expect_any": ["12 tháng 9", "12/9"],
      "stale_any": ["5 tháng 9", "5/9"],
      "note": "Both dates are live approved rows. Prefer the later. Assignee on the 5 Sep create must not be treated as a date."
    },
    {
      "id": "ep_restraint_01",
      "targets": "episodic",
      "test": "restraint",
      "question": "Số hồ sơ trên tác vụ trước về gia hạn CCCD là bao nhiêu?",
      "expect_refusal": true,
      "refusal_about": ["số hồ sơ", "mã hồ sơ"],
      "note": "Neither CCCD episode has a case number. Do not add one."
    },
    {
      "id": "ep_restraint_02",
      "targets": "episodic",
      "test": "restraint",
      "question": "Ai được giao xử lý tác vụ trước về gia hạn CCCD?",
      "expect_refusal": true,
      "refusal_about": ["người phụ trách", "người được giao"],
      "invented_any": ["Phạm Quốc Huy", "Quốc Huy"],
      "note": "Neither CCCD episode names an assignee. Phạm Quốc Huy is on the passport-create episode only. Do not move him onto Hải Phòng CCCD — that would make a true retrieve grade as invented."
    },
    {
      "id": "sem_recall_01",
      "targets": "semantic",
      "test": "recall",
      "question": "Chính sách công ty yêu cầu nộp đề nghị làm thêm giờ qua biểu mẫu nào?",
      "expect_any": ["OT-114", "OT 114"],
      "note": "OT-114 is the overtime form. OT-141 is night-shift only. Citing OT-141 is a lookalike miss. Cue chính sách công ty required."
    },
    {
      "id": "sem_recall_02",
      "targets": "semantic",
      "test": "recall",
      "question": "Chính sách công ty yêu cầu đăng ký làm việc từ xa qua biểu mẫu nào?",
      "expect_any": ["WFH-207", "WFH 207"],
      "note": "Code exists in exactly one line of remote-work-policy.md."
    },
    {
      "id": "sem_recall_03",
      "targets": "semantic",
      "test": "recall",
      "question": "Chính sách công ty quy định công tác phí trong nước mỗi ngày là bao nhiêu?",
      "expect_any": ["450.000", "450000", "450 000"],
      "note": "Domestic figure only. Digit forms listed because models normalise thousands separators."
    },
    {
      "id": "sem_restraint_01",
      "targets": "semantic",
      "test": "restraint",
      "question": "Chính sách công ty nói gì về chế độ nghỉ dài hạn sabbatical?",
      "expect_refusal": true,
      "refusal_about": [
        "chính sách nghỉ dài hạn",
        "chế độ nghỉ dài hạn",
        "chính sách sabbatical",
        "quy định về sabbatical"
      ],
      "note": "Absent topic. Do not declare bare chính sách — a decline about the wrong policy would match it."
    },
    {
      "id": "sem_restraint_02",
      "targets": "semantic",
      "test": "restraint",
      "question": "Chính sách công ty quy định công tác phí cho chuyến đi nước ngoài là bao nhiêu?",
      "expect_refusal": true,
      "refusal_about": [
        "công tác phí nước ngoài",
        "công tác phí cho chuyến đi nước ngoài",
        "mức công tác phí quốc tế"
      ],
      "invented_any": ["450.000"],
      "note": "Near-miss detail. Reciting the domestic 450.000 is invention. Only the verbatim corpus form is listed so the grounding test stays a substring."
    },
    {
      "id": "sem_restraint_03",
      "targets": "semantic",
      "test": "restraint",
      "question": "Chính sách công ty yêu cầu nộp đề nghị đổi laptop hỏng qua biểu mẫu nào?",
      "expect_refusal": true,
      "refusal_about": [
        "biểu mẫu đổi laptop",
        "biểu mẫu đổi thiết bị",
        "mẫu đề nghị đổi máy"
      ],
      "invented_any": ["OT-114", "WFH-207", "OT-141"],
      "note": "Equipment doc has no form code. Reciting any neighbouring code, including the v3 lookalike OT-141, is the failure."
    }
  ]
}
```

- [ ] **Step 2: Run honesty tests (must go green on three files now)**

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run pytest tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py -q
```

Expected: PASS, including v3 in the parametrize ids. If `test_invented_any_phrases_exist_somewhere_in_the_seed` fails, the JSON or corpus is wrong — fix data, not the test. If `test_the_seed_fits_the_prompt_window` fails, you added a 7th ST line — remove it.

- [ ] **Step 3: Dry-run and default launch**

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run python scripts/evaluate_memory.py --probe-set evaluations/MEMORIES/probes/v3-four-scopes-hard.json --dry-run
uv run python -c "from scripts.evaluate_memory import resolve_latest_probe_set; print(resolve_latest_probe_set())"
```

Expected: dry-run exit 0, stderr contains `v3_four_scopes_hard` and 20 probes / 60 calls. `resolve_latest_probe_set()` prints a path ending in `v3-four-scopes-hard.json`.

- [ ] **Step 4: Confirm v1/v2 bytes unchanged**

```powershell
git diff -- evaluations/MEMORIES/probes/v1-four-scopes.json evaluations/MEMORIES/probes/v2-four-scopes-wide.json tests/fixtures/memory_eval/corpus tests/fixtures/memory_eval/corpus-v2
```

Expected: empty.

- [ ] **Step 5: Commit**

Message: `feat(memory-eval): add v3_four_scopes_hard diagnostic probe set`

---

### Task 4: Operator docs — default launch is v3

**Files:**
- Modify: `evaluations/MEMORIES/README.md` (table under “Which probe set”, ~lines 50–56; launch example that still says v2 is the integer-prefix illustration)
- Modify: `evaluations/MEMORIES/RUNBOOK.md` (~lines 122–135 default file name and cost)
- Modify: `.agents/skills/mem-eval/SKILL.md` **only if** a sentence still hardcodes v2 as the launch default. The skill already says “latest `vN-*.json`” — leave it if that is still true.

**Interfaces:**
- Consumes: Task 3 file on disk
- Produces: docs that name v3 as default launch and v1/v2 as pin-able historical sets; report still binds by id + hash

- [ ] **Step 1: README table**

Keep the launch-vs-report split. Change the integer-prefix example to `v3-four-scopes-hard.json → 3`. Add a row:

| file | probes | probe turns per run | what it is |
|---|---|---|---|
| `probes/v3-four-scopes-hard.json` | 20 | 60 | Harder v2 grid: crowded ST, same-shape CCCD distractor, `OT-141` lookalike, more `invented_any`. Corpus `tests/fixtures/memory_eval/corpus-v3/`. Default launch once this file exists. |

Keep v1 and v2 rows. After the table, say v2/v3 are **not comparable** (different `probe_set_id`, different seed). Cost: v3 ≈ 280 model turns (spec §4), one run at a time.

- [ ] **Step 2: RUNBOOK default paragraph**

Replace the paragraph that says both commands run `v2-four-scopes-wide` as default. New text: default launch is latest `vN-*.json` (now `v3-four-scopes-hard.json`). Pin v2 with `--probe-set evaluations/MEMORIES/probes/v2-four-scopes-wide.json`. Cost line: v3 ~280 turns; v2 ~220; v1 ~52.

- [ ] **Step 3: Verify paths exist**

Every path named in the changed sentences must exist (`v3-four-scopes-hard.json`, `corpus-v3/`).

- [ ] **Step 4: Commit**

Message: `docs(mem-eval): default launch is v3_four_scopes_hard`

---

### Task 5: Code-simplifier + honesty/full unit gate

**Files:** only Python from Task 1 (JSON/corpus out of scope for simplifier)

- [ ] **Step 1: Run `code-simplifier`** on `test_probe_set_fires_retrieval.py` if the new test duplicated helpers. Extract a shared `_seed_material(data) -> str` used by both grounding tests **only if** that shortens the file without changing assertions. Do not rename fixtures.

- [ ] **Step 2: Narrow then full suite**

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run pytest tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py tests/unit/scripts/test_evaluate_memory.py -q
uv run ruff check tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py
uv run pytest -q
```

Expected: honesty + evaluate_memory tests pass; ruff clean on the touched test file; full suite green (yellow `DESELECTED - NOT VERIFIED BY THIS RUN` banner for `-m 'not live'` is normal).

- [ ] **Step 3: Commit only if Step 1 changed files**

Message: `refactor(memory-eval): share seed-material helper for honesty tests`

---

### Task 6: Live `/mem-eval` harness verify

Follow `.agents/skills/mem-eval/SKILL.md` end to end. This is **harness verification** (spec §13.2), not a product gate.

**Files (outputs, not source):**
- `evaluations/MEMORIES/baselines/v3-four-scopes-hard-sqlite.json`
- `evaluations/MEMORIES/runs/<stamp>-v3_four_scopes_hard-detail.json`
- `evaluations/MEMORIES/reports/<YYYY-MM-DD>-v3_four_scopes_hard.md` (or whatever the builder writes)

Do not commit `runs/` chat db. Ask before committing the baseline JSON / report (they are run artifacts).

- [ ] **Step 1: Preflight**

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run python scripts/memeval_preflight.py
```

Exit 0 required. Exit 1 → stop, fix deps, do not run. Embeddings stay Gemini.

- [ ] **Step 2: Evaluate with no `--probe-set`** (must load v3)

```powershell
uv run python scripts/evaluate_memory.py --output evaluations/MEMORIES/baselines/v3-four-scopes-hard-sqlite.json
```

One run at a time. If exit 1 and JSON has `"aborted": true`, still do Step 3. Exit 2 → probe set / args / unsafe DB — fix, do not report.

Confirm the JSON `probe_set_id` is `v3_four_scopes_hard` and `probe_set_path` points at `v3-four-scopes-hard.json`.

- [ ] **Step 3: Report (bind by id + hash)**

```powershell
uv run python scripts/build_memory_evaluation_report.py --baseline evaluations/MEMORIES/baselines/v3-four-scopes-hard-sqlite.json
```

Must **not** bind a v2 baseline. Exit 1 on hash mismatch is a harness fail (good). Success = 3-arm matrix present.

- [ ] **Step 4: Triage, do not greenwash**

Use mem-eval §3 (Concerns C → A → B → D). Look in the detail file for `invented_any` hits (Lê Thu Vân / Mai Liên / Phạm Quốc Huy / OT-141 / 450.000) and for `ep_recall_01` ranking misses (Hải Phòng). A low Full-arm pass rate is compatible with success if hardness worked. Do not edit production prompts. Do not weaken the JSON to raise the score.

Write a short verify note in the PR/session summary: launch file, bind id, aborted or not, and whether invented_any / foreign-EP distractor showed up in grades.

- [ ] **Step 5: Commit only if the team wants the baseline in git**

Ask the user. If yes: add the baseline JSON + markdown report, not `memeval-chat.db`. Message: `eval(memory): v3_four_scopes_hard sqlite baseline`

---

## Execution notes

- Create worktree first (`using-git-worktrees`): e.g. `C:\WORK\EMAIL-AGENT-v1\worktrees\memory-eval-probe-set-v3` on `feat/memory-eval-probe-set-v3` from `dev` at `b4e175b` or later.
- Tasks 1 → 2 → 3 are strictly sequential (guard, corpus, JSON). Task 4 docs after JSON exists. Task 5 after 1–4. Task 6 needs keys and must not run until 1–5 are green.
- File-disjoint SDD: Task 1 (tests) and Task 2 (corpus) **can** run in parallel; Task 3 depends on both. Prefer sequential if only one implementer.
- Merge back to root `dev` only after Task 5 (and Task 6 if the live run is required for the workstream — spec §14 item 6 says it is).

## Spec coverage

| Spec section | Task |
|---|---|
| §2 identity, new file/id | 3 |
| §3 Vietnamese only | 3 (constraints) |
| §4 budget / window | 3 + existing window test |
| §5 why harder | 3 notes |
| §6 seed + §6.1 holes | 3 |
| §7 20 probes + invented_any map | 3 |
| §8 corpus-v3 + OT-141 | 2 |
| §9.1 invented_any grounding test | 1 |
| §9.2 operator docs | 4 |
| §9.3 no parent §3 | global |
| §10 out of scope | global |
| §11 commands | 3 dry-run, 6 live |
| §13 honesty table | 3, 5 |
| §13.2 / §14 live verify | 6 |

## Execution choice

After you approve this plan:

1. **Subagent-Driven (recommended)** — fresh implementer per task, review between tasks, `subagent-driven-development`
2. **Inline** — this session, `executing-plans`, checkpoint after each task
