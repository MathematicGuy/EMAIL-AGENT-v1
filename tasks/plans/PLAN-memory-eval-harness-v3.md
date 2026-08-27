# PLAN — Memory Evaluation Harness v3 (shipped v2 probe set)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [SPEC-memory-eval-harness-v3-scalability.md](../specs/SPEC-memory-eval-harness-v3-scalability.md)
**Parent:** [SPEC-memory-evaluation.md](../specs/SPEC-memory-evaluation.md) §3 — **do not amend**
**Dataset:** `evaluations/MEMORIES/probes/v2-four-scopes-wide.json` (`probe_set_id: v2_four_scopes_wide`). No 50-probe file in this plan.
**Branch:** `dev`

**Goal:** Land harness v3 on the shipped v2 probe set: grader cells for `st_restraint_02` and `lt_restraint_01`, `invented_any`, foreign-session episodic fill on every FULL/ABLATED arm, passage-vector cache, consecutive provider circuit breaker, and report-to-baseline probe binding.

**Architecture:** Four spec modules (`refusal-grid`, `seeding-session`, `embedding-cache`, `run-resilience`). Parent 3-arm contract stays: FULL/ABLATED write the full `SeedSpec`; CONTROL is never filled; ABLATED masks the read. Isolation tax (`2 × E × N` episodic seed turns) is not reduced.

**Tech Stack:** existing `uv` / pytest harness under `src/cowork_agent/features/ai_chat/memory_eval/`, `scripts/evaluate_memory.py`, `scripts/build_memory_evaluation_report.py`.

## Global Constraints

- Always `uv run pytest` / `uv run ruff` / `uv run mypy`. Narrowest route first (`tests/README.md` R2 for `features/ai_chat`, R3 if `InRepoSemanticMemory` grows a factory, R9 for the two scripts).
- CONTROL never seeded. ABLATED still writes, then masks `probe.targets`.
- `Probe.targets` stays a single `MemoryType`. `SCHEMA_VERSION` stays `"2.0.0"`.
- Cache files only under `evaluations/MEMORIES/runs/cache/` (already gitignored).
- Do not add `"tôi rất tiếc"`, `"chính sách"`, `"quy định"`, or `"hướng dẫn"` to the shared grader lists.
- Do not skip `seed_long_term` or `seed_episodic` on FULL/ABLATED to save turns.
- No live 50-probe run in this plan. No production chat prompt changes.

---

## File map

| File | Role |
|---|---|
| `src/cowork_agent/features/ai_chat/memory_eval/probes.py` | `invented_any`; `find_probe_set_file` |
| `src/cowork_agent/features/ai_chat/memory_eval/scoring.py` | `_HAVING_NOTHING` cells; invented_any wins over refusal |
| `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py` | `_seed_for` dispatch; consecutive provider breaker; skip-ask |
| `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py` | cache inside `seed_semantic` |
| `src/cowork_agent/integrations/rag/memory.py` | `InRepoSemanticMemory.from_precomputed_matrix` |
| `scripts/evaluate_memory.py` | CLI breaker; baseline `probe_set_path` / `probe_set_sha256`; partial flush |
| `scripts/build_memory_evaluation_report.py` | resolve by `probe_set_id` + hash, never “latest file” |
| `evaluations/MEMORIES/probes/v2-four-scopes-wide.json` | `invented_any` on `st_restraint_02` |
| Tests listed per task | red-green each slice |

---

## Task 1 — Refusal grid + `invented_any` (fixes `st_restraint_02` and `lt_restraint_01`)

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/probes.py`
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/scoring.py`
- Modify: `tests/unit/features/ai_chat/memory_eval/test_probes.py`
- Modify: `tests/unit/features/ai_chat/memory_eval/test_scoring.py`
- Modify: `evaluations/MEMORIES/probes/v2-four-scopes-wide.json` (`st_restraint_02` only)

**Interfaces:**
- Consumes: `Probe.refusal_about`, `score()`, `SCHEMA_VERSION = "2.0.0"`
- Produces: `Probe.invented_any: tuple[str, ...] = ()`; `_HAVING_NOTHING` gains the spec §5.2 / §5.4 forms

- [ ] **Step 1: Failing loader + scoring tests**

Add next to `test_refusal_about_is_parsed` in `test_probes.py`:

```python
def test_invented_any_is_parsed() -> None:
    payload = _payload(
        probes=[
            {
                "id": "st_restraint_02",
                "targets": "short_term",
                "test": "restraint",
                "question": "q",
                "expect_refusal": True,
                "refusal_about": ["người nhận hồ sơ"],
                "invented_any": ["Lê Thu Vân", "Thu Vân"],
            }
        ]
    )
    probe = load_probe_set(payload).probes[0]
    assert probe.invented_any == ("Lê Thu Vân", "Thu Vân")


def test_invented_any_defaults_to_empty() -> None:
    assert load_probe_set(_payload()).probes[0].invented_any == ()


def test_invented_any_without_expect_refusal_is_rejected() -> None:
    payload = _payload(
        probes=[
            {
                "id": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "question": "q",
                "expect_any": ["a turn"],
                "invented_any": ["Lê Thu Vân"],
            }
        ]
    )
    with pytest.raises(ProbeSetError, match="invented_any"):
        load_probe_set(payload)
```

Add to `test_scoring.py` (use the shipped `refusal_about` tuples, not a generic probe):

```python
def test_st_restraint_02_full_arm_refusal_is_a_pass() -> None:
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=("người nhận hồ sơ", "tên người nhận"),
        invented_any=("Lê Thu Vân", "Thu Vân"),
    )
    reply = (
        "Tôi rất tiếc, nhưng các tài liệu hiện có không cung cấp thông tin "
        "về tên của người nhận hồ sơ ở văn phòng Đà Nẵng."
    )
    result = score(reply, probe)
    assert result.outcome is Outcome.PASS
    assert result.certain is False


def test_st_restraint_02_wrap_invention_is_invented() -> None:
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=("người nhận hồ sơ", "tên người nhận"),
        invented_any=("Lê Thu Vân", "Thu Vân"),
    )
    reply = "Các tài liệu không cung cấp thông tin đầy đủ; người nhận là Lê Thu Vân."
    assert score(reply, probe).outcome is Outcome.INVENTED


def test_lt_restraint_01_control_arm_du_hedge_is_a_pass() -> None:
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=("chức danh", "chức vụ"),
    )
    reply = (
        "Tôi không có đủ thông tin để xác định chức danh của bạn. "
        "Vui lòng cung cấp thêm chi tiết hoặc bằng chứng liên quan."
    )
    result = score(reply, probe)
    assert result.outcome is Outcome.PASS
    assert result.certain is False


def test_lt_restraint_01_supplied_title_is_still_invented() -> None:
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=("chức danh", "chức vụ"),
    )
    assert score("Chức danh của bạn là điều phối viên vận hành.", probe).outcome is Outcome.INVENTED


def test_politeness_only_is_not_a_refusal() -> None:
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    assert score("Tôi rất tiếc.", probe).outcome is Outcome.INVENTED
    assert score("Xin lỗi.", probe).outcome is Outcome.INVENTED


def test_sem_restraint_01_wrong_policy_with_khong_co_chinh_sach_stays_invented() -> None:
    probe = _probe(
        expect_any=(),
        expect_refusal=True,
        test=ProbeTest.RESTRAINT,
        refusal_about=(
            "chính sách nghỉ dài hạn",
            "chế độ nghỉ dài hạn",
            "chính sách sabbatical",
            "quy định về sabbatical",
        ),
    )
    reply = (
        "Hiện không có chính sách nghỉ dài hạn sabbatical riêng; "
        "theo quy định nghỉ phép năm nhân viên được 12 ngày."
    )
    # After the cell "không có đủ" / "không cung cấp" land, this recitation
    # must still be INVENTED: "không có chính sách" is NOT a shared noun.
    assert score(reply, probe).outcome is Outcome.INVENTED
```

- [ ] **Step 2: Run tests, confirm red**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_probes.py tests/unit/features/ai_chat/memory_eval/test_scoring.py -q
```

Expected: new tests fail (`invented_any` missing; CONTROL `đủ` reply still `INVENTED`).

- [ ] **Step 3: Minimal implementation**

`Probe`: add `invented_any: tuple[str, ...] = ()`. `_load_probe`: parse with `_string_tuple`; reject when set without `expect_refusal` (copy the `refusal_about` guard).

`_HAVING_NOTHING` append only:

```python
("không cung cấp",)
("chưa cung cấp",)
("không đề cập",)
("chưa đề cập",)
("không đề cập đến",)
("chưa đề cập đến",)
("không có đủ",)
("chưa có đủ",)
```

Do **not** add passives, `"không ghi nhận"`, or policy nouns.

`score()`, restraint branch, after empty-reply:

```python
if probe.expect_refusal:
    if probe.invented_any and _has(reply, probe.invented_any):
        return ScoreResult(
            Outcome.INVENTED, False, "answered a question it was never given the answer to"
        )
    if _has(reply, refusal_phrases_for(probe)):
        return ScoreResult(Outcome.PASS, False, "declined, as it should")
    return ScoreResult(
        Outcome.INVENTED, False, "answered a question it was never given the answer to"
    )
```

`v2-four-scopes-wide.json` `st_restraint_02`: add `"invented_any": ["Lê Thu Vân", "Thu Vân"]`. Do not add `invented_any` to `lt_restraint_01`.

- [ ] **Step 4: Tests green + shipped set still loads**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_probes.py tests/unit/features/ai_chat/memory_eval/test_scoring.py tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py -q
```

Expected: pass. After this slice, `lt_restraint_01` CONTROL sentence grades `PASS`; 3-arm `pass|pass|pass` → `scope_did_nothing` (parent `derive_verdict`). `st_restraint_02` Full-arm sentence grades `PASS`; wrap with `Lê Thu Vân` grades `INVENTED`.

**Dependencies:** none. **Scope:** M.

---

## Task 2 — Foreign-session episodic fill (keep 3-arm stores)

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py` (`_seed_for` only; breaker is Task 4)
- Modify: `tests/unit/features/ai_chat/memory_eval/test_live_runner.py`
- Modify: `tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py` (`test_the_seed_fits_the_prompt_window`)

**Interfaces:**
- Consumes: `seed_long_term`, `seed_episodic`, `seed_short_term`, `needs_fresh_session`, `EpisodeSeed`
- Produces: `_seed_for` always writes LT + EP (foreign `{session_id}-seed`) on FULL/ABLATED; ST buffer only when `probe.targets is MemoryType.SHORT_TERM`

- [ ] **Step 1: Failing tests**

```python
from cowork_agent.features.ai_chat.memory_eval.probes import EpisodeSeed
from cowork_agent.features.ai_chat.memory_eval.seeding import SeedOutcome


def test_a_long_term_probe_still_calls_seed_episodic_in_a_foreign_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[str] = []

    async def fake_episodic(controller, session_id, spec, *, key_prefix):
        del controller, spec, key_prefix
        sessions.append(session_id)
        return SeedOutcome(MemoryType.EPISODIC, True, "ok")

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        fake_episodic,
    )
    session = _session(
        _Reply(), SeedSpec((), {"language": "vi"}, (EpisodeSeed("Tạo một tác vụ.", True),), None)
    )
    asyncio.run(ask_live(session, _probe(targets=MemoryType.LONG_TERM), Arm.FULL, None))
    assert sessions
    assert all(item.endswith("-seed") for item in sessions)


def test_control_still_never_calls_seed_episodic(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    async def fake_episodic(controller, session_id, spec, *, key_prefix):
        called.append(session_id)
        return SeedOutcome(MemoryType.EPISODIC, True, "ok")

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        fake_episodic,
    )
    session = _session(_Reply(), SeedSpec((), {}, (EpisodeSeed("Tạo một tác vụ.", True),), None))
    asyncio.run(ask_live(session, _probe(targets=MemoryType.LONG_TERM), Arm.CONTROL, None))
    assert called == []


def test_short_term_probe_buffer_does_not_contain_episodic_seed_text() -> None:
    seed = SeedSpec(
        ("a seeded line",),
        {},
        (EpisodeSeed("Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.", True),),
        None,
    )
    session = _session(_Reply(), seed)
    asyncio.run(ask_live(session, _probe(targets=MemoryType.SHORT_TERM), Arm.FULL, None))
    assert session.last_gateway is not None
    turns = session.last_gateway._read_active_turns()
    assert any("a seeded line" in (turn.user_message or "") for turn in turns)
    assert not any("Tạo một tác vụ" in (turn.user_message or "") for turn in turns)
```

Change `test_the_seed_fits_the_prompt_window` to:

```python
used = len(seed.short_term) + 1
```

Update its comment: episodic seed turns no longer occupy the probing session. Keep the eviction warning for ST seed + ask only.

- [ ] **Step 2: Run, confirm red**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py::test_the_seed_fits_the_prompt_window -q
```

Expected: LT probe’s `seed_episodic` today runs in `{session_id}-seed` already for non-ST; ST path still appends EP in the probe session → contamination test fails. Window test still uses `+ len(episodic)` → assertion still passes until the comment/formula change is the test itself (formula change may stay green on v2: `4+1=5 <= 8`). Force the formula change in the same task; it documents the new invariant even if currently green.

- [ ] **Step 3: Rewrite `_seed_for` to the spec §4.3 sketch**

Always `seed_long_term`. Always foreign-session `seed_episodic`. `seed_short_term` only if `probe.targets is MemoryType.SHORT_TERM` on the **probe** controller. Delete the else-branch that calls `seed_episodic` through `probe_controller`. CONTROL still never enters `_seed_for` (`ask_live` guard unchanged).

Keep `verify_seed` on `landed` successful scopes. Do not wire the breaker here.

- [ ] **Step 4: Tests green**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py -q
```

**Dependencies:** none (file overlap with Task 4: do Task 2 first). **Scope:** S–M.

---

## Task 3 — Passage-vector cache inside `seed_semantic`

**Files:**
- Modify: `src/cowork_agent/integrations/rag/memory.py` (`from_precomputed_matrix`)
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`
- Modify: `tests/unit/features/ai_chat/memory_eval/test_live_seeding.py`

**Interfaces:**
- Consumes: `load_corpus`, `InRepoSemanticMemory.build_index`, `_Embedder` in `test_live_seeding.py`
- Produces: `async def seed_semantic(..., cache_dir: Path | None = None)`; cache hit ⇒ zero `task="retrieval.passage"` embeds

- [ ] **Step 1: Failing tests** (tmp_path cache dir)

Spy `task` on the existing `_Embedder.embed`. First `seed_semantic` (miss) must call passage embed. Second call with the same embedder identity must not.

```python
def test_semantic_cache_hit_skips_passage_embeds(tmp_path: Path) -> None:
    spec = SeedSpec((), {}, (), _CORPUS)
    first = _Embedder()
    outcome, adapter = asyncio.run(
        seed_semantic(spec, first, corpus_root=Path("."), cache_dir=tmp_path)
    )
    assert outcome.ok is True
    assert adapter is not None
    assert "retrieval.passage" in first.tasks

    second = _Embedder()
    outcome, adapter = asyncio.run(
        seed_semantic(spec, second, corpus_root=Path("."), cache_dir=tmp_path)
    )
    assert outcome.ok is True
    assert "retrieval.passage" not in second.tasks


def test_semantic_cache_misses_when_embedder_identity_changes(tmp_path: Path) -> None:
    spec = SeedSpec((), {}, (), _CORPUS)
    asyncio.run(
        seed_semantic(spec, _Embedder(model="a"), corpus_root=Path("."), cache_dir=tmp_path)
    )
    other = _Embedder(model="b")
    asyncio.run(seed_semantic(spec, other, corpus_root=Path("."), cache_dir=tmp_path))
    assert "retrieval.passage" in other.tasks


def test_corrupt_cache_rebuilds_instead_of_failing_seed(tmp_path: Path) -> None:
    spec = SeedSpec((), {}, (), _CORPUS)
    asyncio.run(seed_semantic(spec, _Embedder(), corpus_root=Path("."), cache_dir=tmp_path))
    for npz in tmp_path.glob("*.npz"):
        npz.write_bytes(b"not-an-npz")
    embedder = _Embedder()
    outcome, adapter = asyncio.run(
        seed_semantic(spec, embedder, corpus_root=Path("."), cache_dir=tmp_path)
    )
    assert outcome.ok is True
    assert adapter is not None
    assert "retrieval.passage" in embedder.tasks
```

Extend `_Embedder` with `model: str = "fake"`, `tasks: list[str]`, and `self.tasks.append(task)` inside `embed`.

- [ ] **Step 2: Run, confirm red**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py -q
```

- [ ] **Step 3: Implement**

`InRepoSemanticMemory.from_precomputed_matrix(documents, embedder, matrix)`:
- same constructor path (chunks, non-empty check)
- require `matrix.ndim == 2`, `dtype == float32`, finite, `shape[0] == len(chunks)`
- store the array; do not assign from outside via `_matrix`

`live_seeding.py`:
- Default cache dir: `Path("evaluations/MEMORIES/runs/cache/embeddings")`
- Key: SHA256 over (format version `1`, embedder identity `type(embedder).__name__` + `getattr(embedder, "model", "")` + `getattr(embedder, "dimensions", "")` + `task=retrieval.passage`, then each `load_corpus` file as POSIX name + length + bytes, then each chunk text). Filename: sanitized `corpus_dir.name` + `_` + full hex + `.npz`
- Hit: `np.load(..., allow_pickle=False)`, copy array, factory; on any validation error delete/ignore and rebuild
- Miss: `build_index`, write temp in the same dir, `os.replace`
- `seed_semantic` signature: add `cache_dir: Path | None = None` so tests inject `tmp_path`. `evaluate_memory.py` needs no call-site change if default is used.

Do not poke `index._matrix`. Do not `await` in a sync `def`.

- [ ] **Step 4: Tests green + R3 smoke if factory is public**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py tests/unit/integrations/rag/test_rag.py -q
```

**Dependencies:** none. **Scope:** M.

---

## Task 4 — Consecutive provider circuit breaker + skip-ask + partial flush

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py`
- Modify: `scripts/evaluate_memory.py`
- Modify: `tests/unit/features/ai_chat/memory_eval/test_live_runner.py`
- Modify: `tests/unit/scripts/test_evaluate_memory.py` (CLI parse / abort flush only if cheap; otherwise keep CLI wiring untested beyond argparse and rely on live_runner tests)

**Interfaces:**
- Consumes: `LiveSession.seed_failures`, `ask_once` errors, `ExcessiveSeedFailuresError`
- Produces: `LiveSession.max_consecutive_provider_failures: int = 3`, `consecutive_provider_failures: int = 0`; env `MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES`; CLI `--max-consecutive-provider-failures`

- [ ] **Step 1: Replace `test_excessive_seed_failures_aborts_run`**

Today it pre-loads 4 strings then seeds an empty spec (rituals succeed) and expects abort. That encoding is wrong under the new breaker.

```python
def test_leftover_seed_failure_strings_do_not_abort() -> None:
    session = _session(_Reply())
    session.seed_failures = ["err1", "err2", "err3", "err4"]
    text, _ = asyncio.run(ask_live(session, _probe(targets=MemoryType.EPISODIC), Arm.FULL, None))
    assert text == "an answer"


def test_control_does_not_reset_consecutive_provider_failures() -> None:
    session = _session(_Reply())
    session.consecutive_provider_failures = 2
    asyncio.run(ask_live(session, _probe(), Arm.CONTROL, None))
    assert session.consecutive_provider_failures == 2


def test_consecutive_provider_seed_failures_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_ep(controller, session_id, spec, *, key_prefix):
        del controller, session_id, spec, key_prefix
        return SeedOutcome(
            MemoryType.EPISODIC,
            False,
            "no task episode was created for seed 0 (chat_provider_unavailable: down)",
        )

    monkeypatch.setattr(
        "cowork_agent.features.ai_chat.memory_eval.live_runner.seed_episodic",
        fail_ep,
    )
    session = _session(_Reply(), SeedSpec((), {}, (EpisodeSeed("Tạo một tác vụ.", True),), None))
    session.max_consecutive_provider_failures = 3
    probe = _probe(targets=MemoryType.EPISODIC)
    with pytest.raises(ExcessiveSeedFailuresError):
        for _ in range(3):
            session.seeded.clear()
            asyncio.run(ask_live(session, probe, Arm.FULL, None))
```

Provider-class detector: `"chat_provider_unavailable"` in the new `seed_failures` lines from this `_seed_for`. Verify findings (`verification read came back empty`) do not increment. Isolated success between failures resets the counter — add a test that two failures, one successful empty-seed FULL, then two more failures, do not abort at max=3.

Skip-ask: when this `_seed_for` is provider-class fail and under the cap, `ask_once` is not called; return `("", 0)` so scoring yields `NO_ANSWER`.

- [ ] **Step 2: Red**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py -q
```

- [ ] **Step 3: Implement**

`LiveSession`: `consecutive_provider_failures: int = 0`, `max_consecutive_provider_failures: int = 3`.

`ask_live`:
- CONTROL: do not touch the counter; never seed.
- FULL/ABLATED: after `_seed_for`, classify **new** ritual failures (not `verify_seed` lines). If provider-class: increment; if `>= max`: raise `ExcessiveSeedFailuresError`; else skip `ask_once`, append an ask_error, return `("", 0)`.
- If LLM-backed rituals succeeded: reset consecutive to 0, then `ask_once`. If `ask_once` errors contain `chat_provider_unavailable`: increment and maybe raise.

CLI: `--max-consecutive-provider-failures`, env `MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES`. Precedence CLI > env > 3. Reject `< 1`. Pass into `LiveSession` when `run_live` builds it.

Partial flush: in `run_live` / `main`, catch `ExcessiveSeedFailuresError` **after** some `ask` records exist, still write baseline + detail from `recorded` / `transcript`, stamp `aborted: true`, then return 1. Today the except in `main` prints and returns 1 with no files — that is the bug spec §7.1 names.

- [ ] **Step 4: Green**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py tests/unit/scripts/test_evaluate_memory.py -q
```

**Dependencies:** Task 2 (same `_seed_for`). **Scope:** M.

---

## Task 5 — Probe-set binding (CLI latest vs report-by-id)

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/probes.py` (`find_probe_set_file`)
- Modify: `scripts/evaluate_memory.py` (stamp path + sha256; keep `resolve_latest_probe_set` for launch only)
- Modify: `scripts/build_memory_evaluation_report.py` (delete v1/v2 substring fallback)
- Create: `tests/unit/features/ai_chat/memory_eval/test_probe_files.py` (or extend `test_probes.py`)
- Create: `tests/unit/scripts/test_build_memory_evaluation_report.py`

**Interfaces:**
- Consumes: `load_probe_set`, `baseline["probe_set_id"]`
- Produces: `find_probe_set_file(probes_dir: Path, probe_set_id: str) -> Path`; baseline keys `probe_set_path`, `probe_set_sha256`

- [ ] **Step 1: Failing tests**

```python
def test_find_probe_set_file_matches_id(tmp_path: Path) -> None:
    v2 = tmp_path / "v2-four-scopes-wide.json"
    v3 = tmp_path / "v3-50-probes.json"
    v2.write_text(_minimal_probe_json("v2_four_scopes_wide"), encoding="utf-8")
    v3.write_text(_minimal_probe_json("v3_50_probes"), encoding="utf-8")
    assert find_probe_set_file(tmp_path, "v2_four_scopes_wide") == v2


def test_find_probe_set_file_unknown_id_fails(tmp_path: Path) -> None:
    (tmp_path / "v2-four-scopes-wide.json").write_text(
        _minimal_probe_json("v2_four_scopes_wide"), encoding="utf-8"
    )
    with pytest.raises(ProbeSetError, match="v3_50_probes"):
        find_probe_set_file(tmp_path, "v3_50_probes")
```

Report builder: given a baseline dict with `probe_set_id: "v2_four_scopes_wide"` and a probes dir that also contains a v3 file, the loaded `ProbeSet.probe_set_id` is still `v2_four_scopes_wide`. Hash mismatch → non-zero / loud error, not a silent v2 fallback.

`evaluate_memory` dry-run (or the function that stamps the report) includes `probe_set_path` and `probe_set_sha256` matching `hashlib.sha256(path.read_bytes()).hexdigest()`.

Keep `resolve_latest_probe_set` for CLI launch; add a test that a dir with `v2-....json` and `v3-....json` returns the v3 **path** (integer prefix), independent of the report helper.

- [ ] **Step 2: Red**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/test_probes.py tests/unit/scripts/test_build_memory_evaluation_report.py tests/unit/scripts/test_evaluate_memory.py -q
```

- [ ] **Step 3: Implement**

`find_probe_set_file`: glob `*.json`, `load_probe_set`, match `probe_set_id`, zero matches → `ProbeSetError`, more than one → `ProbeSetError`.

`build_memory_evaluation_report.py` lines that map `"v2" in id` / `"v1" in id` / else v2: replace with `find_probe_set_file(_DEFAULT_PROBES_DIR, probe_set_id)` when `--probe-set` is omitted. If baseline has `probe_set_sha256`, compare to file bytes; mismatch → print ERROR, return 1.

`evaluate_memory.py` after a successful load: `report["probe_set_path"]` as POSIX relative if possible, `report["probe_set_sha256"]`.

Do not call `resolve_latest_probe_set` from the report builder.

- [ ] **Step 4: Green**

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/ tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_build_memory_evaluation_report.py -q
```

**Dependencies:** Task 1 if tests share `_payload`; otherwise none. **Scope:** M.

---

## Checkpoint — after Tasks 1–5

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/ tests/unit/integrations/rag/test_rag.py tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_build_memory_evaluation_report.py -q
uv run ruff check src/cowork_agent/features/ai_chat/memory_eval src/cowork_agent/integrations/rag/memory.py scripts/evaluate_memory.py scripts/build_memory_evaluation_report.py
uv run mypy src/cowork_agent/features/ai_chat/memory_eval src/cowork_agent/integrations/rag/memory.py scripts/evaluate_memory.py scripts/build_memory_evaluation_report.py
uv run pytest -q
```

Yellow banner `DESELECTED - NOT VERIFIED BY THIS RUN` is expected (`-m 'not live'`). Do not claim a live 50-probe or leaderboard rerun unless the human asks.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `invented_any` first-hit flips a true refusal that mentions `Lê Thu Vân` as the signer while refusing the recipient | Med | Only `st_restraint_02` declares those names; the Full-arm shipped sentence does not contain them |
| Cache key misses chunker params and silently hits | High | Hash ordered chunk texts, not only file bytes |
| Breaker counts verify_seed lines | High | Classify only `chat_provider_unavailable` ritual/ask errors |
| Report still substring-maps v2 | High | `find_probe_set_file` + fail loud |

## Out of scope

- 50-probe dataset (`SPEC-memory-eval-probe-set-v3`)
- Cheaper seed-only model / fewer episodic seed entries
- Amending parent SPEC §3
- Fixing the report diagnostic that scanned FULL to label `lt_restraint_01` Concern A (the **grader** fix in Task 1 makes CONTROL `pass`; verdict becomes `scope_did_nothing` and that diagnostic branch will not fire)

## Spec coverage

| Spec module | Task |
|---|---|
| §5 refusal-grid + §5.4 `lt_restraint_01` | 1 |
| §4 seeding-session | 2 |
| §6 embedding-cache | 3 |
| §7.1 circuit breaker | 4 |
| §7.2 probe resolution | 5 |
| §8 tests | each task + checkpoint |
