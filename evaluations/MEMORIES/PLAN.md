# Memory Evaluation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal memory evaluation harness that asks probes through the real chat controller under three arms and attributes every result to exactly one of our four memory scopes.

**Architecture:** Pure functions first (probe loading, scoring, verdicts, report assembly) so the entire judgment layer is unit-testable offline with no model, no database, and no network. A thin runner on top drives `ChatController.stream_message` three times per probe — once with all memory, once with the probe's target scope masked out by a `MemoryGateway` subclass, and once against an unseeded store. No production code is modified.

**Tech Stack:** Python 3.11+, stdlib dataclasses and `StrEnum`, pytest, existing `cowork_agent.domain.chat_contracts` types, existing `GeminiTransport` protocol for the refusal judge.

**Spec:** [`evaluations/MEMORIES/SPEC.md`](./SPEC.md) — read it before Task 1. The plan argues from the spec; every task cites the section it implements.

## Global Constraints

- Branch: `feat/agent-tool`. All paths are relative to the worktree root `.worktrees/feat/agent-tool/`.
- **pytest pins this checkout's `src` automatically** (`tests/conftest.py`).
  You do not need `PYTHONPATH=src` for pytest. The shared venv's editable
  install still points at the main tree, so raw `python -c "import
  cowork_agent"` (and non-pytest scripts) resolve to main unless you set
  `PYTHONPATH=src`.
- **Do not pass `-n 0` or `-p no:xdist`.** The suite already fans out to 4
  workers. `-p no:xdist` used to usage-error because `-n` lived in `addopts`;
  that is fixed (`tests/xdist_plugin.py`). Use `-n 0` only to debug one
  failure. Focused routes keep the default workers.
- Python `>=3.11`; ruff `target-version = "py311"`, `line-length = 100`, lint rules `["E", "F", "I", "UP", "B"]`.
- `mypy --strict` passes on `src/`. Everything under `src/cowork_agent/features/ai_chat/memory_eval/` is in scope for it.
- Every module starts with `from __future__ import annotations`.
- Dataclasses are `@dataclass(frozen=True, slots=True)`, matching the surrounding feature package.
- **No production file is modified.** `controller.py`, `retrieval_policy.py`, `memory_gateway.py` and the domain contracts are read-only for this plan. The only exceptions are `.gitignore` (Task 9) and new files.
- **Reuse `MemoryType`** from `cowork_agent.domain.chat_contracts` for scope identity. Do not define a parallel scope enum.
- **Never name a class `Test*`** — pytest collects those. The probe test-type enum is `ProbeTest`.
- `schema_version` is `"1.0.0"` on both probe sets and reports.
- **Committed reports are metadata-only**: case IDs, counts, verdicts, timings, provider/model identifiers. No probe `question`, no reply text, no seed text. Task 4 enforces this with a test.
- Tests that need a real model or a real database are marked `@pytest.mark.live` (already registered in `pyproject.toml`, deselected by default).
- Commit after every task using the message given in that task's final step.

---

## File Structure

| File | Responsibility | Pure? |
|---|---|---|
| `src/cowork_agent/features/ai_chat/memory_eval/__init__.py` | Package exports | yes |
| `src/cowork_agent/features/ai_chat/memory_eval/probes.py` | `Probe`, `ProbeSet`, `SeedSpec`, `EpisodeSeed`, `ProbeTest`, JSON loader + validation | yes |
| `src/cowork_agent/features/ai_chat/memory_eval/scoring.py` | `Outcome`, `ScoreResult`, `score()`, refusal phrase list | yes |
| `src/cowork_agent/features/ai_chat/memory_eval/verdicts.py` | `Verdict`, `derive_verdict()`, `asserts_recall()`, scoreboard ordering | yes |
| `src/cowork_agent/features/ai_chat/memory_eval/report.py` | `ProbeRow`, `build_report()`, metadata-only enforcement | yes |
| `src/cowork_agent/features/ai_chat/memory_eval/arms.py` | `Arm`, `mask_reads()`, `mask_request()`, `ArmScopedMemoryGateway` | mostly |
| `src/cowork_agent/features/ai_chat/memory_eval/judge.py` | `RefusalJudge` protocol, `GeminiRefusalJudge`, `NullRefusalJudge` | no |
| `src/cowork_agent/features/ai_chat/memory_eval/seeding.py` | The four seeding rituals, `SeedOutcome` | no |
| `src/cowork_agent/features/ai_chat/memory_eval/runner.py` | `run_probe_set()` — orchestrates arms, seeding, scoring | no |
| `scripts/evaluate_memory.py` | CLI: argument parsing, `--dry-run`, report writing, exit codes | no |
| `evaluations/MEMORIES/probes/v1-four-scopes.json` | The committed probe set | data |
| `evaluations/MEMORIES/README.md` | How to run it, how to read a report | docs |
| `tests/fixtures/memory_eval/corpus/*.md` | Tiny synthetic company-policy corpus | data |

Tasks 1–6 produce the offline tier that gates CI. Tasks 7–8 produce the live tier that measures and does not gate. Task 9 is the content and documentation.

---

## Task 1: Probe contracts and loader

Implements SPEC §4. Foundational — every later task consumes these types.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/__init__.py`
- Create: `src/cowork_agent/features/ai_chat/memory_eval/probes.py`
- Test: `tests/unit/features/ai_chat/memory_eval/__init__.py` (empty)
- Test: `tests/unit/features/ai_chat/memory_eval/test_probes.py`

**Interfaces:**
- Consumes: `MemoryType` from `cowork_agent.domain.chat_contracts`.
- Produces: `ProbeTest`, `EpisodeSeed`, `SeedSpec`, `Probe`, `ProbeSet`, `load_probe_set(payload: Mapping[str, object]) -> ProbeSet`, `ProbeSetError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/__init__.py` as an empty file, then `tests/unit/features/ai_chat/memory_eval/test_probes.py`:

```python
from __future__ import annotations

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import (
    ProbeSetError,
    ProbeTest,
    load_probe_set,
)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "1.0.0",
        "probe_set_id": "unit",
        "label": "unit probe set",
        "seed": {
            "short_term": ["a turn"],
            "long_term": {"language": "vi"},
            "episodic": [{"request": "Create a task to renew", "approve": True}],
            "semantic": {"corpus_dir": "tests/fixtures/memory_eval/corpus"},
        },
        "probes": [
            {
                "id": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "question": "what did I say?",
                "expect_any": ["a turn"],
            }
        ],
    }
    base.update(overrides)
    return base


def test_loads_a_minimal_probe_set() -> None:
    probe_set = load_probe_set(_payload())
    assert probe_set.probe_set_id == "unit"
    assert len(probe_set.probes) == 1
    probe = probe_set.probes[0]
    assert probe.probe_id == "st_recall_01"
    assert probe.targets is MemoryType.SHORT_TERM
    assert probe.test is ProbeTest.RECALL
    assert probe.expect_any == ("a turn",)
    assert probe.expect_all == ()
    assert probe.stale_any == ()
    assert probe.expect_refusal is False
    assert probe.foreign_seed is False


def test_seed_is_parsed_into_typed_fields() -> None:
    seed = load_probe_set(_payload()).seed
    assert seed.short_term == ("a turn",)
    assert seed.long_term == {"language": "vi"}
    assert seed.episodic[0].request == "Create a task to renew"
    assert seed.episodic[0].approve is True
    assert seed.semantic_corpus_dir == "tests/fixtures/memory_eval/corpus"


def test_probe_with_no_expectation_is_rejected() -> None:
    payload = _payload(
        probes=[
            {"id": "bad", "targets": "short_term", "test": "recall", "question": "q"}
        ]
    )
    with pytest.raises(ProbeSetError, match="expectation"):
        load_probe_set(payload)


def test_duplicate_probe_ids_are_rejected() -> None:
    probe = {
        "id": "dupe",
        "targets": "short_term",
        "test": "recall",
        "question": "q",
        "expect_any": ["x"],
    }
    with pytest.raises(ProbeSetError, match="unique"):
        load_probe_set(_payload(probes=[probe, dict(probe)]))


def test_unsafe_probe_id_is_rejected() -> None:
    payload = _payload(
        probes=[
            {
                "id": "bad id!",
                "targets": "short_term",
                "test": "recall",
                "question": "q",
                "expect_any": ["x"],
            }
        ]
    )
    with pytest.raises(ProbeSetError, match="identifier"):
        load_probe_set(payload)


def test_unknown_scope_is_rejected() -> None:
    payload = _payload(
        probes=[
            {
                "id": "p",
                "targets": "procedural",
                "test": "recall",
                "question": "q",
                "expect_any": ["x"],
            }
        ]
    )
    with pytest.raises(ProbeSetError, match="targets"):
        load_probe_set(payload)


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ProbeSetError, match="schema_version"):
        load_probe_set(_payload(schema_version="2.0.0"))


def test_foreign_seed_requires_the_isolation_test_type() -> None:
    payload = _payload(
        probes=[
            {
                "id": "p",
                "targets": "semantic",
                "test": "recall",
                "question": "q",
                "expect_any": ["x"],
                "foreign_seed": True,
            }
        ]
    )
    with pytest.raises(ProbeSetError, match="foreign_seed"):
        load_probe_set(payload)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_probes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cowork_agent.features.ai_chat.memory_eval'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/__init__.py`:

```python
"""Minimal memory evaluation harness. See evaluations/MEMORIES/SPEC.md."""
```

Create `src/cowork_agent/features/ai_chat/memory_eval/probes.py`:

```python
"""Probe set contracts and loader (SPEC §4).

A probe is one question with a declared expectation and exactly one target
scope. Targeting is declared, never inferred: the whole harness rests on being
able to say which scope a result belongs to, and guessing that from the
question text would make every verdict an opinion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cowork_agent.domain.chat_contracts import MemoryType

SCHEMA_VERSION = "1.0.0"
_MAX_ID_LENGTH = 64


class ProbeSetError(ValueError):
    """The probe set is not loadable as specified."""


class ProbeTest(StrEnum):
    """What kind of failure this probe is designed to catch (SPEC §4.2)."""

    RECALL = "recall"
    UPDATE = "update"
    RESTRAINT = "restraint"
    ISOLATION = "isolation"


@dataclass(frozen=True, slots=True)
class EpisodeSeed:
    """One task episode to create during seeding, and whether to approve it.

    ``approve`` matters: a freshly written episode is retrieval_eligible=false
    by policy, so an unapproved seed is deliberately unreadable. That is a
    valid thing to seed — it is how you prove the eligibility gate works.
    """

    request: str
    approve: bool


@dataclass(frozen=True, slots=True)
class SeedSpec:
    """What to put in each of the four scopes before probing (SPEC §6)."""

    short_term: tuple[str, ...]
    long_term: Mapping[str, str]
    episodic: tuple[EpisodeSeed, ...]
    semantic_corpus_dir: str | None


@dataclass(frozen=True, slots=True)
class Probe:
    probe_id: str
    targets: MemoryType
    test: ProbeTest
    question: str
    expect_any: tuple[str, ...] = ()
    expect_all: tuple[str, ...] = ()
    stale_any: tuple[str, ...] = ()
    expect_refusal: bool = False
    foreign_seed: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class ProbeSet:
    schema_version: str
    probe_set_id: str
    label: str
    seed: SeedSpec
    probes: tuple[Probe, ...]


def _safe_id(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_ID_LENGTH
        or not value.replace("_", "").replace("-", "").isalnum()
    ):
        raise ProbeSetError(f"{field} must be a safe opaque identifier")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ProbeSetError(f"{field} must be a list of strings")
    for item in value:
        if not isinstance(item, str) or not item:
            raise ProbeSetError(f"{field} must contain only non-empty strings")
    return tuple(str(item) for item in value)


def _bool(value: object, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ProbeSetError(f"{field} must be a boolean")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProbeSetError(f"{field} must be an object")
    return value


def _load_probe(data: Mapping[str, object]) -> Probe:
    probe_id = _safe_id(data.get("id"), "probe id")
    raw_target = data.get("targets")
    try:
        targets = MemoryType(raw_target)
    except ValueError as error:
        raise ProbeSetError(
            f"probe {probe_id}: targets must be one of "
            f"{[member.value for member in MemoryType]}"
        ) from error
    try:
        test = ProbeTest(data.get("test"))
    except ValueError as error:
        raise ProbeSetError(f"probe {probe_id}: unknown test type") from error

    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ProbeSetError(f"probe {probe_id}: question must be a non-empty string")

    expect_any = _string_tuple(data.get("expect_any"), f"probe {probe_id}: expect_any")
    expect_all = _string_tuple(data.get("expect_all"), f"probe {probe_id}: expect_all")
    stale_any = _string_tuple(data.get("stale_any"), f"probe {probe_id}: stale_any")
    expect_refusal = _bool(data.get("expect_refusal"), f"probe {probe_id}: expect_refusal")
    foreign_seed = _bool(data.get("foreign_seed"), f"probe {probe_id}: foreign_seed")

    # A probe with no expectation always passes, which is worse than no probe.
    if not (expect_any or expect_all or expect_refusal):
        raise ProbeSetError(
            f"probe {probe_id}: must declare an expectation "
            "(expect_any, expect_all, or expect_refusal)"
        )
    if foreign_seed and test is not ProbeTest.ISOLATION:
        raise ProbeSetError(
            f"probe {probe_id}: foreign_seed is only meaningful with test 'isolation'"
        )

    note = data.get("note", "")
    if not isinstance(note, str):
        raise ProbeSetError(f"probe {probe_id}: note must be a string")

    return Probe(
        probe_id=probe_id,
        targets=targets,
        test=test,
        question=question,
        expect_any=expect_any,
        expect_all=expect_all,
        stale_any=stale_any,
        expect_refusal=expect_refusal,
        foreign_seed=foreign_seed,
        note=note,
    )


def _load_seed(data: Mapping[str, object]) -> SeedSpec:
    long_term_raw = _mapping(data.get("long_term", {}), "seed.long_term")
    long_term: dict[str, str] = {}
    for key, value in long_term_raw.items():
        if not isinstance(value, str):
            raise ProbeSetError(f"seed.long_term.{key} must be a string")
        long_term[str(key)] = value

    episodic_raw = data.get("episodic", [])
    if isinstance(episodic_raw, str) or not isinstance(episodic_raw, Sequence):
        raise ProbeSetError("seed.episodic must be a list")
    episodic: list[EpisodeSeed] = []
    for entry in episodic_raw:
        entry_map = _mapping(entry, "seed.episodic entry")
        request = entry_map.get("request")
        if not isinstance(request, str) or not request.strip():
            raise ProbeSetError("seed.episodic entry needs a non-empty request")
        episodic.append(
            EpisodeSeed(request=request, approve=_bool(entry_map.get("approve"), "approve"))
        )

    semantic_dir: str | None = None
    semantic_raw = data.get("semantic")
    if semantic_raw is not None:
        semantic_map = _mapping(semantic_raw, "seed.semantic")
        corpus_dir = semantic_map.get("corpus_dir")
        if corpus_dir is not None and not isinstance(corpus_dir, str):
            raise ProbeSetError("seed.semantic.corpus_dir must be a string")
        semantic_dir = corpus_dir

    return SeedSpec(
        short_term=_string_tuple(data.get("short_term"), "seed.short_term"),
        long_term=long_term,
        episodic=tuple(episodic),
        semantic_corpus_dir=semantic_dir,
    )


def load_probe_set(payload: Mapping[str, object]) -> ProbeSet:
    """Parse and validate a probe set. Raises ProbeSetError on anything unusable."""

    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ProbeSetError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {version!r}"
        )
    probe_set_id = _safe_id(payload.get("probe_set_id"), "probe_set_id")
    label = payload.get("label", probe_set_id)
    if not isinstance(label, str):
        raise ProbeSetError("label must be a string")

    probes_raw = payload.get("probes")
    if isinstance(probes_raw, str) or not isinstance(probes_raw, Sequence) or not probes_raw:
        raise ProbeSetError("probes must be a non-empty list")
    probes = tuple(_load_probe(_mapping(entry, "probe")) for entry in probes_raw)

    seen = {probe.probe_id for probe in probes}
    if len(seen) != len(probes):
        raise ProbeSetError("probe ids must be unique")

    return ProbeSet(
        schema_version=SCHEMA_VERSION,
        probe_set_id=probe_set_id,
        label=label,
        seed=_load_seed(_mapping(payload.get("seed", {}), "seed")),
        probes=probes,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_probes.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval
git commit -m "feat(memory-eval): probe set contracts and loader"
```

---

## Task 2: Scoring — the four outcomes

Implements SPEC §8. Pure function over strings; no model, no I/O.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/scoring.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_scoring.py`

**Interfaces:**
- Consumes: `Probe` from Task 1.
- Produces: `Outcome` (StrEnum: `PASS`/`STALE`/`INVENTED`/`MISS`), `ScoreResult(outcome: Outcome, certain: bool, why: str)`, `score(reply: str, probe: Probe) -> ScoreResult`, `REFUSAL_PHRASES: tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_scoring.py`:

```python
from __future__ import annotations

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeTest
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome, score


def _probe(**overrides: object) -> Probe:
    defaults: dict[str, object] = {
        "probe_id": "p",
        "targets": MemoryType.SHORT_TERM,
        "test": ProbeTest.RECALL,
        "question": "q",
        "expect_any": ("Wednesday",),
    }
    defaults.update(overrides)
    return Probe(**defaults)  # type: ignore[arg-type]


def test_expected_answer_present_is_a_pass() -> None:
    result = score("It is on Wednesday.", _probe())
    assert result.outcome is Outcome.PASS
    assert result.certain is True


def test_matching_is_case_insensitive() -> None:
    assert score("it is on wednesday", _probe()).outcome is Outcome.PASS


def test_expected_absent_with_superseded_present_is_stale() -> None:
    probe = _probe(stale_any=("Tuesday",))
    result = score("It is on Tuesday.", probe)
    assert result.outcome is Outcome.STALE
    assert "Tuesday" in result.why


def test_expected_present_alongside_superseded_is_still_a_pass() -> None:
    # "Wednesday, it moved from Tuesday" is the most helpful phrasing available.
    # Scoring it STALE would penalise the best answer. SPEC §8.2.
    probe = _probe(stale_any=("Tuesday",))
    assert score("Wednesday - it moved from Tuesday.", probe).outcome is Outcome.PASS


def test_expected_absent_with_no_superseded_is_a_miss() -> None:
    result = score("I have nothing on that.", _probe())
    assert result.outcome is Outcome.MISS


def test_refusal_expected_and_declined_is_a_pass_but_uncertain() -> None:
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    result = score("I don't have that information.", probe)
    assert result.outcome is Outcome.PASS
    assert result.certain is False


def test_refusal_expected_and_answered_is_invented_and_uncertain() -> None:
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    result = score("The case number is 55-A.", probe)
    assert result.outcome is Outcome.INVENTED
    assert result.certain is False


def test_expect_all_partially_present_is_a_miss_naming_what_is_missing() -> None:
    probe = _probe(expect_any=(), expect_all=("Marcus", "Thursday"))
    result = score("Marcus is unavailable.", probe)
    assert result.outcome is Outcome.MISS
    assert "Thursday" in result.why


def test_expect_all_fully_present_is_a_pass() -> None:
    probe = _probe(expect_any=(), expect_all=("Marcus", "Thursday"))
    assert score("Marcus is out on Thursday.", probe).outcome is Outcome.PASS


def test_superseded_answer_with_no_expectation_declared_is_stale() -> None:
    probe = _probe(expect_any=(), expect_all=("Marcus",), stale_any=("Tuesday",))
    result = score("Marcus said Tuesday.", probe)
    assert result.outcome is Outcome.STALE


def test_empty_reply_is_a_miss_not_a_crash() -> None:
    assert score("", _probe()).outcome is Outcome.MISS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_scoring.py -v`
Expected: FAIL — `ImportError: cannot import name 'Outcome'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/scoring.py`:

```python
"""Grading one reply against one probe (SPEC §8).

Four outcomes, not pass/fail. A system that says "I don't know" is behaving
correctly under uncertainty; a system that confidently returns a superseded
answer, or invents one, is dangerous. Both look identical on a boolean.

Everything here is a pure function over strings so the whole judgment layer is
testable with no model, no key, and no network. Only the refusal branch is
uncertain, and it says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .probes import Probe


class Outcome(StrEnum):
    PASS = "pass"
    STALE = "stale"
    INVENTED = "invented"
    MISS = "miss"


# How models decline when they genuinely have nothing. Deliberately about the
# ABSENCE OF KNOWLEDGE, not politeness — "I'm sorry" also opens plenty of
# confidently wrong answers, so it is not on this list.
#
# This list will never be complete. Every verdict resting on it is returned
# with certain=False so a judge can settle it (SPEC §8.3); a missed phrasing
# would otherwise score an honest refusal as INVENTED, which is the worst
# direction to be wrong in.
REFUSAL_PHRASES: tuple[str, ...] = (
    "don't know",
    "do not know",
    "not sure",
    "no information",
    "no record",
    "never told",
    "never mentioned",
    "never gave",
    "didn't tell",
    "did not tell",
    "didn't mention",
    "did not mention",
    "haven't told",
    "have not told",
    "you haven't",
    "you have not",
    "not in my memory",
    "don't have",
    "do not have",
    "nothing about",
    "nothing shared",
    "no details",
    "not specified",
    "wasn't specified",
    "unable to find",
    "couldn't find",
    "could not find",
    "khong co thong tin",
    "khong ro",
)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    outcome: Outcome
    certain: bool
    why: str


def _has(haystack: str, needles: Sequence[str]) -> bool:
    low = haystack.casefold()
    return any(needle.casefold() in low for needle in needles)


def score(reply: str, probe: Probe) -> ScoreResult:
    """Grade one reply. Returns the outcome, whether it is certain, and why.

    ``certain=False`` marks a verdict that rests on REFUSAL_PHRASES and is
    therefore worth one judge call. Everything else is a substring check and
    needs no model at all.
    """

    reply = reply or ""

    if probe.expect_refusal:
        if _has(reply, REFUSAL_PHRASES):
            return ScoreResult(Outcome.PASS, False, "declined, as it should")
        return ScoreResult(
            Outcome.INVENTED, False, "answered a question it was never given the answer to"
        )

    # STALE fires only when the expected answer is ABSENT. A reply that gives
    # the right answer and also mentions the superseded one is a good reply.
    if probe.expect_any and not _has(reply, probe.expect_any):
        if probe.stale_any and _has(reply, probe.stale_any):
            return ScoreResult(
                Outcome.STALE, True, f"asserted the superseded answer ({probe.stale_any[0]})"
            )
        return ScoreResult(Outcome.MISS, True, "expected answer absent")

    if probe.expect_all:
        missing = [item for item in probe.expect_all if not _has(reply, (item,))]
        if missing:
            return ScoreResult(
                Outcome.MISS, True, f"only part of the answer - missing {missing}"
            )

    if not probe.expect_any and probe.stale_any and _has(reply, probe.stale_any):
        return ScoreResult(Outcome.STALE, True, "asserted a superseded answer")

    return ScoreResult(Outcome.PASS, True, "correct")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_scoring.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/scoring.py tests/unit/features/ai_chat/memory_eval/test_scoring.py
git commit -m "feat(memory-eval): four-outcome scoring with uncertainty flag"
```

---

## Task 3: Verdicts and leak detection

Implements SPEC §9. Turns three outcomes into one plain-language conclusion.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/verdicts.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_verdicts.py`

**Interfaces:**
- Consumes: `Probe` (Task 1), `Outcome` (Task 2).
- Produces: `Verdict` (StrEnum), `asserts_recall(probe: Probe) -> bool`, `derive_verdict(probe: Probe, full: Outcome, ablated: Outcome, control: Outcome) -> Verdict`, `VERDICT_ORDER: tuple[Verdict, ...]`, `verdict_rank(verdict: Verdict) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_verdicts.py`:

```python
from __future__ import annotations

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeTest
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.ai_chat.memory_eval.verdicts import (
    Verdict,
    asserts_recall,
    derive_verdict,
    verdict_rank,
)


def _probe(**overrides: object) -> Probe:
    defaults: dict[str, object] = {
        "probe_id": "p",
        "targets": MemoryType.EPISODIC,
        "test": ProbeTest.RECALL,
        "question": "q",
        "expect_any": ("x",),
    }
    defaults.update(overrides)
    return Probe(**defaults)  # type: ignore[arg-type]


def test_scope_earned_it_when_only_the_full_arm_passes() -> None:
    verdict = derive_verdict(_probe(), Outcome.PASS, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.SCOPE_EARNED_IT


def test_scope_did_nothing_when_ablation_still_passes() -> None:
    verdict = derive_verdict(_probe(), Outcome.PASS, Outcome.PASS, Outcome.MISS)
    assert verdict is Verdict.SCOPE_DID_NOTHING


def test_control_passing_a_recall_probe_is_a_leak() -> None:
    verdict = derive_verdict(_probe(), Outcome.PASS, Outcome.MISS, Outcome.PASS)
    assert verdict is Verdict.LEAKED


def test_broken_when_the_full_arm_fails() -> None:
    verdict = derive_verdict(_probe(), Outcome.MISS, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.BROKEN


def test_invented_anywhere_outranks_every_other_verdict() -> None:
    verdict = derive_verdict(_probe(), Outcome.INVENTED, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.DANGEROUS


def test_stale_anywhere_is_dangerous() -> None:
    verdict = derive_verdict(_probe(), Outcome.STALE, Outcome.MISS, Outcome.MISS)
    assert verdict is Verdict.DANGEROUS


def test_dangerous_beats_leaked() -> None:
    # A control pass AND an invented answer: the invention is the headline.
    verdict = derive_verdict(_probe(), Outcome.INVENTED, Outcome.MISS, Outcome.PASS)
    assert verdict is Verdict.DANGEROUS


def test_refusal_probes_never_count_as_leaks() -> None:
    # An empty store declines every time, so a control PASS here is expected
    # and would otherwise be flagged in every run forever. SPEC §9.2.
    probe = _probe(expect_any=(), expect_refusal=True, test=ProbeTest.RESTRAINT)
    assert asserts_recall(probe) is False
    verdict = derive_verdict(probe, Outcome.PASS, Outcome.PASS, Outcome.PASS)
    assert verdict is not Verdict.LEAKED


def test_asserts_recall_is_true_for_a_content_probe() -> None:
    assert asserts_recall(_probe()) is True


def test_verdict_ordering_puts_dangerous_first_and_earned_last() -> None:
    ordered = sorted(
        [Verdict.SCOPE_EARNED_IT, Verdict.DANGEROUS, Verdict.LEAKED, Verdict.BROKEN],
        key=verdict_rank,
    )
    assert ordered[0] is Verdict.DANGEROUS
    assert ordered[-1] is Verdict.SCOPE_EARNED_IT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_verdicts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.verdicts'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/verdicts.py`:

```python
"""Turning three outcomes into one readable conclusion (SPEC §9).

The scoreboard is sorted worst-behaviour-first: a system that invents ranks
below one that misses, so the interesting column is never buried under a wall
of passes.
"""

from __future__ import annotations

from enum import StrEnum

from .probes import Probe
from .scoring import Outcome


class Verdict(StrEnum):
    DANGEROUS = "dangerous"
    BROKEN = "broken"
    LEAKED = "leaked"
    SCOPE_DID_NOTHING = "scope_did_nothing"
    SCOPE_EARNED_IT = "scope_earned_it"


# Worst first. This is the read order for a human scanning a run.
VERDICT_ORDER: tuple[Verdict, ...] = (
    Verdict.DANGEROUS,
    Verdict.BROKEN,
    Verdict.LEAKED,
    Verdict.SCOPE_DID_NOTHING,
    Verdict.SCOPE_EARNED_IT,
)

_DANGEROUS_OUTCOMES = frozenset({Outcome.STALE, Outcome.INVENTED})


def verdict_rank(verdict: Verdict) -> int:
    return VERDICT_ORDER.index(verdict)


def asserts_recall(probe: Probe) -> bool:
    """Whether a control PASS on this probe means anything.

    Only probes that assert RECALLED CONTENT can leak. A refusal probe is
    passed by declining, and a store with nothing in it declines every time —
    flagging those would mark them in every run, forever, and mean nothing.
    """

    return bool(probe.expect_any or probe.expect_all) and not probe.expect_refusal


def derive_verdict(
    probe: Probe, full: Outcome, ablated: Outcome, control: Outcome
) -> Verdict:
    """Collapse one probe's three arm outcomes into a single conclusion."""

    if full in _DANGEROUS_OUTCOMES or ablated in _DANGEROUS_OUTCOMES:
        return Verdict.DANGEROUS
    if control in _DANGEROUS_OUTCOMES:
        return Verdict.DANGEROUS
    if control is Outcome.PASS and asserts_recall(probe):
        return Verdict.LEAKED
    if full is not Outcome.PASS:
        return Verdict.BROKEN
    if ablated is Outcome.PASS:
        return Verdict.SCOPE_DID_NOTHING
    return Verdict.SCOPE_EARNED_IT
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_verdicts.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/verdicts.py tests/unit/features/ai_chat/memory_eval/test_verdicts.py
git commit -m "feat(memory-eval): verdict derivation and narrow leak detection"
```

---

## Task 4: Report assembly with metadata-only enforcement

Implements SPEC §10. The test that no probe text reaches the committed report is the point of this task.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/report.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_report.py`

**Interfaces:**
- Consumes: `Probe`, `ProbeSet` (Task 1), `Outcome` (Task 2), `Verdict`, `derive_verdict`, `verdict_rank` (Task 3).
- Produces: `ProbeRow`, `build_report(...) -> dict[str, object]`, `REPORT_SCHEMA_VERSION`.

`ProbeRow` fields: `probe_id: str`, `targets: MemoryType`, `test: ProbeTest`, `full: Outcome`, `ablated: Outcome`, `control: Outcome`, `certain: bool`, `latency_ms: int`.

`build_report` signature:

```python
def build_report(
    probe_set: ProbeSet,
    rows: Sequence[ProbeRow],
    *,
    provider: str,
    model: str,
    judge_model: str | None,
    run_key: str,
    ran_at: datetime,
    seed_failures: Sequence[str] = (),
    unscorable_probes: Sequence[str] = (),
    degraded_sources_seen: Sequence[str] = (),
) -> dict[str, object]: ...
```

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_report.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import (
    Probe,
    ProbeSet,
    ProbeTest,
    SeedSpec,
)
from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow, build_report
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome

_SECRET_QUESTION = "what is the unmistakable secret deadline"


def _probe_set() -> ProbeSet:
    return ProbeSet(
        schema_version="1.0.0",
        probe_set_id="unit",
        label="unit",
        seed=SeedSpec(("a seeded sentence",), {}, (), None),
        probes=(
            Probe(
                probe_id="ep_recall_01",
                targets=MemoryType.EPISODIC,
                test=ProbeTest.RECALL,
                question=_SECRET_QUESTION,
                expect_any=("x",),
            ),
        ),
    )


def _row(**overrides: object) -> ProbeRow:
    defaults: dict[str, object] = {
        "probe_id": "ep_recall_01",
        "targets": MemoryType.EPISODIC,
        "test": ProbeTest.RECALL,
        "full": Outcome.PASS,
        "ablated": Outcome.MISS,
        "control": Outcome.MISS,
        "certain": True,
        "latency_ms": 1840,
    }
    defaults.update(overrides)
    return ProbeRow(**defaults)  # type: ignore[arg-type]


def _report(**kwargs: object) -> dict[str, object]:
    return build_report(
        _probe_set(),
        [_row()],
        provider="gemini",
        model="model-id",
        judge_model=None,
        run_key="a1b2c3d4e5f6",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
        **kwargs,  # type: ignore[arg-type]
    )


def test_report_carries_schema_version_and_provenance() -> None:
    report = _report()
    assert report["schema_version"] == "1.0.0"
    assert report["probe_set_id"] == "unit"
    assert report["provider"] == "gemini"
    assert report["model"] == "model-id"
    assert report["run_key"] == "a1b2c3d4e5f6"


def test_report_contains_no_probe_or_seed_text() -> None:
    # The rule from evaluations/HARNESS-GUIDE.md, enforced rather than trusted.
    serialized = json.dumps(_report())
    assert _SECRET_QUESTION not in serialized
    assert "a seeded sentence" not in serialized


def test_verdict_row_is_derived_from_the_three_arms() -> None:
    verdicts = _report()["verdicts"]
    assert isinstance(verdicts, list)
    assert verdicts[0]["verdict"] == "scope_earned_it"
    assert verdicts[0]["probe"] == "ep_recall_01"
    assert verdicts[0]["latency_ms"] == 1840


def test_per_scope_counts_every_scope_even_when_unprobed() -> None:
    per_scope = _report()["per_scope"]
    assert isinstance(per_scope, dict)
    assert set(per_scope) == {"short_term", "long_term", "episodic", "semantic"}
    assert per_scope["episodic"]["probes"] == 1
    assert per_scope["episodic"]["earned_it"] == 1
    assert per_scope["short_term"]["probes"] == 0


def test_leaked_probes_are_named() -> None:
    report = build_report(
        _probe_set(),
        [_row(control=Outcome.PASS)],
        provider="gemini",
        model="m",
        judge_model=None,
        run_key="k",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert report["leaked_probes"] == ["ep_recall_01"]


def test_needs_judge_counts_uncertain_rows() -> None:
    report = build_report(
        _probe_set(),
        [_row(certain=False)],
        provider="gemini",
        model="m",
        judge_model=None,
        run_key="k",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert report["needs_judge"] == 1


def test_verdicts_are_sorted_worst_first() -> None:
    probe_set = _probe_set()
    extra = Probe(
        probe_id="ep_recall_02",
        targets=MemoryType.EPISODIC,
        test=ProbeTest.RECALL,
        question="q2",
        expect_any=("x",),
    )
    probe_set = ProbeSet(
        probe_set.schema_version,
        probe_set.probe_set_id,
        probe_set.label,
        probe_set.seed,
        (*probe_set.probes, extra),
    )
    report = build_report(
        probe_set,
        [_row(), _row(probe_id="ep_recall_02", full=Outcome.STALE)],
        provider="gemini",
        model="m",
        judge_model=None,
        run_key="k",
        ran_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    verdicts = report["verdicts"]
    assert isinstance(verdicts, list)
    assert verdicts[0]["verdict"] == "dangerous"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.report'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/report.py`:

```python
"""Metadata-only report assembly (SPEC §10).

The committed artifact carries case ids, counts, verdicts, timings and model
identifiers — and nothing else. No probe question, no reply, no seed text. A
report that leaks the corpus it was scored against cannot be committed, and
"we were careful" is not an enforcement mechanism, so a test asserts it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain.chat_contracts import MemoryType

from .probes import ProbeSet, ProbeTest
from .scoring import Outcome
from .verdicts import Verdict, derive_verdict, verdict_rank

REPORT_SCHEMA_VERSION = "1.0.0"

_VERDICT_COUNT_KEYS: dict[Verdict, str] = {
    Verdict.DANGEROUS: "dangerous",
    Verdict.BROKEN: "broken",
    Verdict.LEAKED: "leaked",
    Verdict.SCOPE_DID_NOTHING: "did_nothing",
    Verdict.SCOPE_EARNED_IT: "earned_it",
}


@dataclass(frozen=True, slots=True)
class ProbeRow:
    """One probe's outcomes across all three arms."""

    probe_id: str
    targets: MemoryType
    test: ProbeTest
    full: Outcome
    ablated: Outcome
    control: Outcome
    certain: bool
    latency_ms: int


def _empty_scope_counts() -> dict[str, int]:
    counts = {"probes": 0, "pass": 0, "stale": 0, "invented": 0, "miss": 0}
    counts.update({key: 0 for key in _VERDICT_COUNT_KEYS.values()})
    return counts


def build_report(
    probe_set: ProbeSet,
    rows: Sequence[ProbeRow],
    *,
    provider: str,
    model: str,
    judge_model: str | None,
    run_key: str,
    ran_at: datetime,
    seed_failures: Sequence[str] = (),
    unscorable_probes: Sequence[str] = (),
    degraded_sources_seen: Sequence[str] = (),
) -> dict[str, object]:
    by_id = {probe.probe_id: probe for probe in probe_set.probes}
    per_scope: dict[str, dict[str, int]] = {
        member.value: _empty_scope_counts() for member in MemoryType
    }

    entries: list[tuple[int, dict[str, object]]] = []
    leaked: list[str] = []
    needs_judge = 0

    for row in rows:
        probe = by_id.get(row.probe_id)
        if probe is None:
            raise ValueError(f"row references unknown probe {row.probe_id!r}")
        verdict = derive_verdict(probe, row.full, row.ablated, row.control)
        bucket = per_scope[row.targets.value]
        bucket["probes"] += 1
        bucket[row.full.value] += 1
        bucket[_VERDICT_COUNT_KEYS[verdict]] += 1
        if verdict is Verdict.LEAKED:
            leaked.append(row.probe_id)
        if not row.certain:
            needs_judge += 1
        entries.append(
            (
                verdict_rank(verdict),
                {
                    "probe": row.probe_id,
                    "targets": row.targets.value,
                    "test": row.test.value,
                    "full": row.full.value,
                    "ablated": row.ablated.value,
                    "control": row.control.value,
                    "verdict": verdict.value,
                    "certain": row.certain,
                    "latency_ms": row.latency_ms,
                },
            )
        )

    entries.sort(key=lambda item: (item[0], str(item[1]["probe"])))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "probe_set_id": probe_set.probe_set_id,
        "probe_count": len(probe_set.probes),
        "provider": provider,
        "model": model,
        "judge_model": judge_model,
        "ran_at": ran_at.isoformat(),
        "run_key": run_key,
        "per_scope": per_scope,
        "verdicts": [entry for _, entry in entries],
        "leaked_probes": sorted(leaked),
        "unscorable_probes": sorted(unscorable_probes),
        "needs_judge": needs_judge,
        "seed_failures": sorted(seed_failures),
        "degraded_sources_seen": sorted(set(degraded_sources_seen)),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_report.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/report.py tests/unit/features/ai_chat/memory_eval/test_report.py
git commit -m "feat(memory-eval): metadata-only report assembly"
```

---

## Task 5: Arm masking and the gateway seam

Implements SPEC §5. This is the task that lets an ablation arm exist without touching production code.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/arms.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_arms.py`

**Interfaces:**
- Consumes: `MemoryType`, `MemoryReadOptions`, `MemoryContextRequest`, `EpisodicMemoryRead`, `SemanticMemoryRead` from `cowork_agent.domain.chat_contracts`; `MemoryGateway` from `..memory_gateway`.
- Produces: `Arm` (StrEnum `FULL`/`ABLATED`/`CONTROL`), `mask_reads(reads, scope) -> MemoryReadOptions`, `mask_request(request, scope) -> MemoryContextRequest`, `ArmScopedMemoryGateway`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_arms.py`:

```python
from __future__ import annotations

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryQuery,
    SemanticMemoryRead,
)
from cowork_agent.features.ai_chat.memory_eval.arms import mask_reads, mask_request


def _reads() -> MemoryReadOptions:
    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryQuery(query="q", max_items=5, min_score=0.6, timeout_ms=500),
        semantic=SemanticMemoryQuery(query="q", max_items=5, min_score=0.6, timeout_ms=500),
    )


def test_masking_none_changes_nothing() -> None:
    reads = _reads()
    assert mask_reads(reads, None) == reads


def test_masking_short_term_turns_it_off_and_leaves_the_rest() -> None:
    masked = mask_reads(_reads(), MemoryType.SHORT_TERM)
    assert masked.short_term is False
    assert masked.long_term is True
    assert isinstance(masked.episodic, EpisodicMemoryQuery)
    assert isinstance(masked.semantic, SemanticMemoryQuery)


def test_masking_long_term_turns_it_off() -> None:
    masked = mask_reads(_reads(), MemoryType.LONG_TERM)
    assert masked.long_term is False
    assert masked.short_term is True


def test_masking_episodic_swaps_in_the_disabled_read() -> None:
    masked = mask_reads(_reads(), MemoryType.EPISODIC)
    assert isinstance(masked.episodic, EpisodicMemoryRead)
    assert masked.episodic.enabled is False
    assert isinstance(masked.semantic, SemanticMemoryQuery)


def test_masking_semantic_swaps_in_the_disabled_read() -> None:
    masked = mask_reads(_reads(), MemoryType.SEMANTIC)
    assert isinstance(masked.semantic, SemanticMemoryRead)
    assert masked.semantic.enabled is False
    assert isinstance(masked.episodic, EpisodicMemoryQuery)


def test_masking_an_already_disabled_read_is_idempotent() -> None:
    reads = MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=1),
        semantic=SemanticMemoryRead(enabled=False),
    )
    assert mask_reads(reads, MemoryType.EPISODIC) == reads


def test_mask_request_preserves_scope_and_session() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    request = MemoryContextRequest(session_id="s", scope=scope, reads=_reads())
    masked = mask_request(request, MemoryType.LONG_TERM)
    assert masked.scope == scope
    assert masked.session_id == "s"
    assert masked.reads.long_term is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_arms.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.arms'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/arms.py`:

```python
"""Ablation arms and the gateway seam that makes them possible (SPEC §5).

`retrieval_policy.select_memory_reads` is called inside the controller, so
there is no parameter to pass an arm through. Rather than add a test-only
override to production code, the harness swaps in a gateway subclass that
reports one scope as unavailable.

Masking the READ rather than the store is the honest model of an arm: the
question is "what does the reply look like when this scope cannot be read",
and that is exactly what a gateway expresses.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import Any

from cowork_agent.domain.chat_contracts import (
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryRead,
)

from ..memory_gateway import MemoryGateway

# The same explicitly-disabled read objects retrieval_policy.py builds when a
# cue is absent. Reusing the exact shapes keeps a masked arm indistinguishable
# from a genuine no-cue turn.
_DISABLED_EPISODIC = EpisodicMemoryRead(
    enabled=False, retrieval_eligible_only=True, max_items=1
)
_DISABLED_SEMANTIC = SemanticMemoryRead(enabled=False)


class Arm(StrEnum):
    FULL = "full"
    ABLATED = "ablated"
    CONTROL = "control"


def mask_reads(reads: MemoryReadOptions, scope: MemoryType | None) -> MemoryReadOptions:
    """Return `reads` with one scope forced off. `None` returns it unchanged."""

    if scope is None:
        return reads
    if scope is MemoryType.SHORT_TERM:
        return replace(reads, short_term=False)
    if scope is MemoryType.LONG_TERM:
        return replace(reads, long_term=False)
    if scope is MemoryType.EPISODIC:
        return replace(reads, episodic=_DISABLED_EPISODIC)
    return replace(reads, semantic=_DISABLED_SEMANTIC)


def mask_request(
    request: MemoryContextRequest, scope: MemoryType | None
) -> MemoryContextRequest:
    """Mask one scope out of a context request, preserving scope and session."""

    return replace(request, reads=mask_reads(request.reads, scope))


class ArmScopedMemoryGateway(MemoryGateway):
    """A gateway that reports one scope as unavailable, for ablation arms.

    Only `read_context` is overridden. Writes, project-document reads and
    episode transitions are inherited unchanged, because an arm is a statement
    about what can be READ, not about what the system may store.
    """

    def __init__(
        self, *args: Any, masked_scope: MemoryType | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._masked_scope = masked_scope

    async def read_context(self, request: MemoryContextRequest) -> MemoryContextResponse:
        return await super().read_context(mask_request(request, self._masked_scope))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_arms.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Verify the subclass does not break gateway behaviour**

Run: `python -m pytest tests/unit/features/ai_chat/test_memory_gateway.py -q`
Expected: PASS — the existing 44 gateway tests are unaffected, since no production file changed.

- [ ] **Step 6: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/arms.py tests/unit/features/ai_chat/memory_eval/test_arms.py
git commit -m "feat(memory-eval): ablation arms via a scope-masking gateway subclass"
```

---

## Task 6: The refusal judge

Implements SPEC §8.3. One binary question, asked only when the phrase list was the deciding factor.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/judge.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_judge.py`

**Interfaces:**
- Consumes: `Outcome`, `ScoreResult` (Task 2); `GeminiTransport` from `cowork_agent.integrations.llm.providers.gemini`.
- Produces: `RefusalJudge` (Protocol with `async def adjudicate(self, question: str, reply: str) -> bool | None`), `NullRefusalJudge`, `GeminiRefusalJudge`, `reconcile(result: ScoreResult, declined: bool | None) -> ScoreResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_judge.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from cowork_agent.features.ai_chat.memory_eval.judge import (
    GeminiRefusalJudge,
    NullRefusalJudge,
    reconcile,
)
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome, ScoreResult


class _Transport:
    def __init__(self, payload: Mapping[str, Any] | Exception) -> None:
        self._payload = payload
        self.calls = 0

    async def generate(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls += 1
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_null_judge_always_returns_none() -> None:
    assert asyncio.run(NullRefusalJudge().adjudicate("q", "a")) is None


def test_gemini_judge_reads_the_declined_flag() -> None:
    judge = GeminiRefusalJudge(_Transport({"declined": True}), api_key="k", model="m")
    assert asyncio.run(judge.adjudicate("q", "a")) is True


def test_gemini_judge_returns_none_when_the_transport_fails() -> None:
    # An unreachable judge must not be converted into either verdict.
    judge = GeminiRefusalJudge(_Transport(RuntimeError("boom")), api_key="k", model="m")
    assert asyncio.run(judge.adjudicate("q", "a")) is None


def test_gemini_judge_returns_none_on_a_malformed_payload() -> None:
    judge = GeminiRefusalJudge(_Transport({"nope": 1}), api_key="k", model="m")
    assert asyncio.run(judge.adjudicate("q", "a")) is None


def test_judge_overrules_a_false_invented() -> None:
    heuristic = ScoreResult(Outcome.INVENTED, False, "answered")
    settled = reconcile(heuristic, declined=True)
    assert settled.outcome is Outcome.PASS
    assert settled.certain is True
    assert "overruled" in settled.why


def test_judge_overrules_a_false_pass() -> None:
    heuristic = ScoreResult(Outcome.PASS, False, "declined, as it should")
    settled = reconcile(heuristic, declined=False)
    assert settled.outcome is Outcome.INVENTED
    assert settled.certain is True


def test_judge_agreement_only_settles_certainty() -> None:
    heuristic = ScoreResult(Outcome.PASS, False, "declined, as it should")
    settled = reconcile(heuristic, declined=True)
    assert settled.outcome is Outcome.PASS
    assert settled.certain is True
    assert "judge agreed" in settled.why


def test_unreachable_judge_leaves_the_heuristic_standing_and_uncertain() -> None:
    heuristic = ScoreResult(Outcome.INVENTED, False, "answered")
    settled = reconcile(heuristic, declined=None)
    assert settled == heuristic
    assert settled.certain is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.judge'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/judge.py`:

```python
"""The judge that settles refusal verdicts (SPEC §8.3).

The refusal phrase list can never be complete, so every verdict resting on it
is flagged uncertain and sent here — one binary question, wording explicitly
out of scope. A judge that cannot be reached returns None and changes nothing:
"I could not check" is its own state and must never be silently converted into
either verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .scoring import Outcome, ScoreResult

_JUDGE_TIMEOUT_SECONDS = 20

_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {"declined": {"type": "boolean"}},
    "required": ["declined"],
}

_PROMPT = """A user asked an assistant a question the assistant was never told the answer to.
The only correct behaviour is to DECLINE - to say it does not know or has nothing on file.

Question: {question}
Reply: {reply}

Did the reply DECLINE, or did it assert an answer? Wording varies enormously;
judge the meaning, not the phrasing. A reply that declines and then adds general
knowledge clearly marked as general knowledge still counts as declining."""


class _Transport(Protocol):
    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        schema: Mapping[str, object],
        timeout_seconds: int,
        system_instruction: str | None = None,
    ) -> Mapping[str, Any]: ...


class RefusalJudge(Protocol):
    async def adjudicate(self, question: str, reply: str) -> bool | None:
        """True if the reply declined, False if it asserted, None if unreachable."""
        ...


class NullRefusalJudge:
    """Used by --dry-run and by any run without a judge key configured."""

    async def adjudicate(self, question: str, reply: str) -> bool | None:
        del question, reply
        return None


class GeminiRefusalJudge:
    """Binary refusal adjudication over the existing Gemini transport.

    The judge should not be the model under test. Pass a different model id
    than the one driving the probes; a contestant grading its own round is not
    credible.
    """

    def __init__(self, transport: _Transport, *, api_key: str, model: str) -> None:
        self._transport = transport
        self._api_key = api_key
        self._model = model

    async def adjudicate(self, question: str, reply: str) -> bool | None:
        try:
            payload = await self._transport.generate(
                api_key=self._api_key,
                model=self._model,
                prompt=_PROMPT.format(question=question, reply=reply),
                schema=_SCHEMA,
                timeout_seconds=_JUDGE_TIMEOUT_SECONDS,
            )
        except Exception:
            return None
        declined = payload.get("declined")
        if not isinstance(declined, bool):
            return None
        return declined


def reconcile(result: ScoreResult, declined: bool | None) -> ScoreResult:
    """Settle an uncertain heuristic verdict against the judge's answer."""

    if declined is None:
        return result
    if declined and result.outcome is Outcome.INVENTED:
        return ScoreResult(
            Outcome.PASS, True, "declined - judge overruled the phrase list"
        )
    if not declined and result.outcome is Outcome.PASS:
        return ScoreResult(
            Outcome.INVENTED, True, "asserted an answer - judge overruled the phrase list"
        )
    return ScoreResult(result.outcome, True, f"{result.why} (judge agreed)")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_judge.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/judge.py tests/unit/features/ai_chat/memory_eval/test_judge.py
git commit -m "feat(memory-eval): binary refusal judge with fail-quiet semantics"
```

---

## Task 7: Seeding rituals

Implements SPEC §6. Each scope is seeded through the path a real user would take.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/seeding.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_seeding.py`

**Interfaces:**
- Consumes: `SeedSpec` (Task 1); `MemoryGateway`, `ChatMemoryScope`, `DeclarativeProfile`, `MemoryProvenance`, `MemoryProvenanceSource`, `MemoryType`, `PROFILE_PREFERENCE_FIELDS`.
- Produces: `SeedOutcome(scope: MemoryType, ok: bool, reason: str)`, `build_seed_profile(scope, fields, *, now, profile_id) -> DeclarativeProfile`, `async def seed_long_term(gateway, scope, spec, *, now, profile_id) -> SeedOutcome`.

**Scope is passed explicitly, not read off the gateway.** `MemoryGateway`
stores its scope as the private `self._scope` and exposes no accessor
(verified: `grep -n "def scope" memory_gateway.py` returns nothing). Reaching
into a private attribute from the harness would couple this module to an
implementation detail, and the global constraints forbid adding a property to
the production class. So `seed_long_term` takes a `ChatMemoryScope` parameter.

**Only the `long_term` ritual is in this task.** The `short_term`, `episodic`
and `semantic` rituals all drive `ChatController.stream_message`, which belongs
to the live tier deferred in Task 8. `long_term` is the one ritual that needs
nothing but the gateway, so it ships here and proves the pattern.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_seeding.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    DeclarativeProfile,
    MemoryNamespace,
    MemoryProvenanceSource,
    MemoryType,
)
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec
from cowork_agent.features.ai_chat.memory_eval.seeding import (
    SeedOutcome,
    build_seed_profile,
    seed_long_term,
)


class _Declarative:
    def __init__(self, *, fail: bool = False) -> None:
        self.written: list[DeclarativeProfile] = []
        self._fail = fail

    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        del namespace
        return self.written[-1] if self.written else None

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        del namespace
        if self._fail:
            raise RuntimeError("adapter down")
        self.written.append(profile)
        return profile

    async def delete_profile(self, namespace: MemoryNamespace) -> bool:
        del namespace
        return True


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")


def test_build_seed_profile_maps_only_known_preference_fields() -> None:
    profile = build_seed_profile(
        _scope(),
        {"language": "vi", "response_tone": "concise"},
        now=datetime(2026, 8, 18, tzinfo=UTC),
        profile_id="prof-1",
    )
    assert profile.language == "vi"
    assert profile.response_tone == "concise"
    assert profile.assistant_persona is None
    assert profile.source_type is MemoryProvenanceSource.EXPLICIT_USER_CONFIG


def test_build_seed_profile_rejects_an_unknown_field() -> None:
    with pytest.raises(ValueError, match="unknown profile field"):
        build_seed_profile(
            _scope(),
            {"nickname": "x"},
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="prof-1",
        )


def test_seed_long_term_writes_with_explicit_user_config_provenance(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    # memory_gateway_factory is defined in this module's conftest (Step 2).
    declarative = _Declarative()
    gateway = memory_gateway_factory(declarative_memory=declarative)
    spec = SeedSpec((), {"language": "vi"}, (), None)
    outcome = asyncio.run(
        seed_long_term(
            gateway,
            _scope(),
            spec,
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="p1",
        )
    )
    assert outcome == SeedOutcome(MemoryType.LONG_TERM, True, "seeded")
    assert declarative.written[0].language == "vi"
    assert declarative.written[0].source_type is MemoryProvenanceSource.EXPLICIT_USER_CONFIG


def test_seed_long_term_reports_a_failure_instead_of_raising(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    # A seeding failure is a finding about the scope, not a reason to abandon
    # the other three. SPEC §6.1.
    gateway = memory_gateway_factory(declarative_memory=_Declarative(fail=True))
    spec = SeedSpec((), {"language": "vi"}, (), None)
    outcome = asyncio.run(
        seed_long_term(
            gateway,
            _scope(),
            spec,
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="p1",
        )
    )
    assert outcome.ok is False
    assert "adapter down" in outcome.reason


def test_seed_long_term_is_skipped_when_no_profile_is_declared(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory(declarative_memory=_Declarative())
    outcome = asyncio.run(
        seed_long_term(
            gateway,
            _scope(),
            SeedSpec((), {}, (), None),
            now=datetime(2026, 8, 18, tzinfo=UTC),
            profile_id="p1",
        )
    )
    assert outcome.ok is True
    assert outcome.reason == "nothing declared"
```

- [ ] **Step 2: Write the shared gateway fixture**

Create `tests/unit/features/ai_chat/memory_eval/conftest.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer


@pytest.fixture
def memory_gateway_factory() -> Callable[..., MemoryGateway]:
    """Build a MemoryGateway with a real session buffer and injectable adapters."""

    def _factory(**adapters: Any) -> MemoryGateway:
        return MemoryGateway(
            scope=ChatMemoryScope(tenant_id="t", user_id="u", session_id="s"),
            session_buffer=InMemoryChatSessionBuffer(max_turns=20, ttl_seconds=1800),
            **adapters,
        )

    return _factory
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_seeding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.seeding'`

- [ ] **Step 4: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/seeding.py`:

```python
"""Seeding each scope through its real authorization path (SPEC §6).

waku-agent seeds conversationally because pushing facts in through the side
door would skip extraction - the step that decides what is worth keeping. Our
equivalent step is AUTHORIZATION. Writing rows straight into the repositories
would score retrieval as if authorization had happened when it had not, and
would let probes pass on episode states no real flow can reach.

A seeding failure is a finding, reported as such, not an exception that takes
the other three scopes down with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from cowork_agent.domain.chat_contracts import (
    PROFILE_PREFERENCE_FIELDS,
    ChatMemoryScope,
    DeclarativeProfile,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryType,
)

from ..memory_gateway import MemoryGateway
from .probes import SeedSpec


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    scope: MemoryType
    ok: bool
    reason: str


def build_seed_profile(
    scope: ChatMemoryScope,
    fields: Mapping[str, str],
    *,
    now: datetime,
    profile_id: str,
) -> DeclarativeProfile:
    """Build a DeclarativeProfile from the probe set's declared preferences.

    Only the four fields in PROFILE_PREFERENCE_FIELDS are accepted. An unknown
    key is a probe-set authoring error and is refused loudly here rather than
    silently dropped, which would make a probe fail for an invisible reason.
    """

    unknown = set(fields) - set(PROFILE_PREFERENCE_FIELDS)
    if unknown:
        raise ValueError(f"unknown profile field(s): {sorted(unknown)}")
    return DeclarativeProfile(
        profile_id=profile_id,
        user_id=scope.user_id,
        language=fields.get("language"),
        timezone=fields.get("timezone"),
        assistant_persona=fields.get("assistant_persona"),
        response_tone=fields.get("response_tone"),
        created_at=now,
        updated_at=now,
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
    )


async def seed_long_term(
    gateway: MemoryGateway,
    scope: ChatMemoryScope,
    spec: SeedSpec,
    *,
    now: datetime,
    profile_id: str,
) -> SeedOutcome:
    """Write the declared profile with explicit_user_config provenance.

    `scope` is passed rather than read off the gateway: MemoryGateway keeps its
    scope private and exposes no accessor, and the harness may not add one.
    """

    if not spec.long_term:
        return SeedOutcome(MemoryType.LONG_TERM, True, "nothing declared")
    try:
        profile = build_seed_profile(scope, spec.long_term, now=now, profile_id=profile_id)
        await gateway.write_profile(
            profile,
            provenance=MemoryProvenance(
                source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
                source_id=profile_id,
                chat_turn_id=None,
                pipeline_version=None,
                model_id=None,
                prompt_version=None,
            ),
        )
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return SeedOutcome(MemoryType.LONG_TERM, False, f"{type(error).__name__}: {error}")
    return SeedOutcome(MemoryType.LONG_TERM, True, "seeded")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_seeding.py -v`
Expected: PASS — 5 passed

- [ ] **Step 6: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/seeding.py tests/unit/features/ai_chat/memory_eval/conftest.py tests/unit/features/ai_chat/memory_eval/test_seeding.py
git commit -m "feat(memory-eval): long-term seeding through the explicit authorization path"
```

---

## Task 8: Runner and CLI

Implements SPEC §7 and §14. Everything below the runner is already tested; this task wires it.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/runner.py`
- Create: `scripts/evaluate_memory.py`
- Test: `tests/unit/scripts/test_evaluate_memory.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: `run_key(probe_set_id: str, model: str, seed: SeedSpec) -> str`, `async def run_probe_set(...) -> dict[str, object]`, and a `main(argv) -> int` in the script.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/scripts/test_evaluate_memory.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec
from cowork_agent.features.ai_chat.memory_eval.runner import run_key
from scripts.evaluate_memory import main


def _probe_set_file(tmp_path: Path) -> Path:
    payload = {
        "schema_version": "1.0.0",
        "probe_set_id": "unit",
        "label": "unit",
        "seed": {"short_term": ["a turn"], "long_term": {}, "episodic": [], "semantic": None},
        "probes": [
            {
                "id": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "question": "what did I say?",
                "expect_any": ["a turn"],
            }
        ],
    }
    path = tmp_path / "probes.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_run_key_is_stable_for_the_same_inputs() -> None:
    seed = SeedSpec(("a",), {}, (), None)
    assert run_key("set", "model", seed) == run_key("set", "model", seed)


def test_run_key_changes_when_the_seed_changes() -> None:
    assert run_key("set", "model", SeedSpec(("a",), {}, (), None)) != run_key(
        "set", "model", SeedSpec(("b",), {}, (), None)
    )


def test_run_key_changes_when_the_model_changes() -> None:
    seed = SeedSpec(("a",), {}, (), None)
    assert run_key("set", "model-a", seed) != run_key("set", "model-b", seed)


def test_dry_run_writes_a_report_and_exits_zero(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    code = main(
        ["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0.0"
    assert report["probe_set_id"] == "unit"
    assert len(report["verdicts"]) == 1


def test_dry_run_report_contains_no_probe_text(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    main(["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)])
    assert "what did I say?" not in output.read_text(encoding="utf-8")


def test_an_invalid_probe_set_exits_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "9.9.9"}), encoding="utf-8")
    assert main(["--dry-run", "--probe-set", str(bad)]) == 2


def test_a_missing_probe_set_exits_two(tmp_path: Path) -> None:
    assert main(["--dry-run", "--probe-set", str(tmp_path / "nope.json")]) == 2


@pytest.mark.live
def test_live_run_requires_a_database_and_key() -> None:
    pytest.skip("live tier: run manually with DATABASE_URL and a provider key set")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/scripts/test_evaluate_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.evaluate_memory'`

- [ ] **Step 3: Write the runner**

Create `src/cowork_agent/features/ai_chat/memory_eval/runner.py`:

```python
"""Orchestrating one probe set across three arms (SPEC §7).

Deliberately linear. The comments name what each step prevents, because every
one of them exists to stop the harness measuring something other than memory.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from cowork_agent.domain.chat_contracts import MemoryType

from .arms import Arm
from .judge import NullRefusalJudge, RefusalJudge, reconcile
from .probes import Probe, ProbeSet, SeedSpec
from .report import ProbeRow, build_report
from .scoring import Outcome, score

# A callable the runner uses to ask one probe under one arm and get the reply
# text plus how long it took. The live implementation drives
# ChatController.stream_message; --dry-run supplies a scripted one.
AskProbe = Callable[[Probe, Arm, MemoryType | None], Awaitable[tuple[str, int]]]


def run_key(probe_set_id: str, model: str, seed: SeedSpec) -> str:
    """A stable id for this exact (probe set, model, seed) combination.

    It names the throwaway tenant so a run can never collide with another run
    or touch a real user's memory, and it is a staleness guard by construction:
    change the seed and you address a different tenant, so a run can never
    quietly probe a store that was seeded for a different question.
    """

    material = json.dumps(
        {
            "probe_set_id": probe_set_id,
            "model": model,
            "short_term": list(seed.short_term),
            "long_term": dict(sorted(seed.long_term.items())),
            "episodic": [[entry.request, entry.approve] for entry in seed.episodic],
            "semantic": seed.semantic_corpus_dir,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


async def run_probe_set(
    probe_set: ProbeSet,
    ask: AskProbe,
    *,
    provider: str,
    model: str,
    ran_at: datetime,
    judge: RefusalJudge | None = None,
    seed_failures: Sequence[str] = (),
    degraded_sources_seen: Sequence[str] = (),
) -> dict[str, object]:
    """Ask every probe under all three arms and assemble the report."""

    judge = judge or NullRefusalJudge()
    rows: list[ProbeRow] = []

    for probe in probe_set.probes:
        outcomes: dict[Arm, Outcome] = {}
        certain = True
        latency_total = 0

        for arm in (Arm.FULL, Arm.ABLATED, Arm.CONTROL):
            # FULL and CONTROL read every scope; only ABLATED masks one. CONTROL
            # differs by having no seed at all, not by disabling reads - see
            # SPEC §5.1, this is the distinction the whole leak signal rests on.
            masked = probe.targets if arm is Arm.ABLATED else None
            reply, latency_ms = await ask(probe, arm, masked)
            latency_total += latency_ms

            result = score(reply, probe)
            if not result.certain:
                # Only refusal verdicts are uncertain, and only those are worth
                # a judge call. An unreachable judge changes nothing.
                result = reconcile(result, await judge.adjudicate(probe.question, reply))
            if not result.certain:
                certain = False
            outcomes[arm] = result.outcome

        rows.append(
            ProbeRow(
                probe_id=probe.probe_id,
                targets=probe.targets,
                test=probe.test,
                full=outcomes[Arm.FULL],
                ablated=outcomes[Arm.ABLATED],
                control=outcomes[Arm.CONTROL],
                certain=certain,
                latency_ms=latency_total,
            )
        )

    return build_report(
        probe_set,
        rows,
        provider=provider,
        model=model,
        judge_model=None if isinstance(judge, NullRefusalJudge) else model,
        run_key=run_key(probe_set.probe_set_id, model, probe_set.seed),
        ran_at=ran_at,
        seed_failures=seed_failures,
        degraded_sources_seen=degraded_sources_seen,
    )
```

- [ ] **Step 4: Write the CLI**

Create `scripts/evaluate_memory.py`:

```python
#!/usr/bin/env python3
"""Memory evaluation harness CLI. See evaluations/MEMORIES/SPEC.md.

Exit codes:
  0 - the run completed and a report was written
  1 - a seed failure made the run unscorable
  2 - the probe set could not be loaded

Exit code 0 does NOT mean the memory system is good. It means the harness ran.
Verdicts are read by a human; this harness reports, it does not gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, load_probe_set
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_set

_DEFAULT_PROBE_SET = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")
_DEFAULT_OUTPUT_DIR = Path("evaluations/MEMORIES/baselines")


def _scripted_ask(probe: Probe, arm: Arm, masked: object) -> tuple[str, int]:
    """A deterministic stand-in reply, for --dry-run only.

    It answers correctly under FULL and declines otherwise, which exercises the
    scoring, verdict and report paths without a model. It measures NOTHING
    about the real system and must never be used to make a decision.
    """

    del masked
    if arm is Arm.FULL:
        if probe.expect_refusal:
            return ("I don't have that information.", 0)
        return (" ".join(probe.expect_any or probe.expect_all), 0)
    return ("I don't have that information.", 0)


async def _dry_run(probe_set: object) -> dict[str, object]:
    async def ask(probe: Probe, arm: Arm, masked: object) -> tuple[str, int]:
        return _scripted_ask(probe, arm, masked)

    return await run_probe_set(
        probe_set,  # type: ignore[arg-type]
        ask,
        provider="dry-run",
        model="scripted",
        ran_at=datetime.now(UTC),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-set", type=Path, default=_DEFAULT_PROBE_SET)
    parser.add_argument("--output", type=Path, help="Report path; defaults under baselines/")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scripted replies. Validates harness mechanics only - never a result.",
    )
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.probe_set.read_text(encoding="utf-8"))
        probe_set = load_probe_set(payload)
    except (OSError, ValueError) as error:
        # ProbeSetError subclasses ValueError, so this catches both a missing
        # file and an unloadable probe set. Listing it separately would be a
        # redundant handler (ruff B014).
        print(f"ERROR: cannot load probe set: {error}", file=sys.stderr)
        return 2

    if not args.dry_run:
        print(
            "ERROR: the live tier is not implemented yet; run with --dry-run",
            file=sys.stderr,
        )
        return 2

    report = asyncio.run(_dry_run(probe_set))

    output = args.output
    if output is None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        output = _DEFAULT_OUTPUT_DIR / f"{stamp}-{probe_set.probe_set_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Note for the implementer:** the live tier (driving the real
`ChatController`) is deliberately left as an explicit `return 2` with a clear
message rather than a silent no-op. Wiring it is the first follow-up task after
this plan; the seam is the `AskProbe` callable, so nothing above it changes.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/scripts/test_evaluate_memory.py -v`
Expected: PASS — 7 passed, 1 skipped (the `live`-marked test is deselected by default)

- [ ] **Step 6: Run the dry run end to end**

Run: `python scripts/evaluate_memory.py --dry-run --probe-set evaluations/MEMORIES/probes/v1-four-scopes.json --output evaluations/MEMORIES/runs/dry-run.json`
Expected: exit 0, a printed report with `"provider": "dry-run"`.
(If Task 9 has not run yet, use a temporary probe set file instead.)

- [ ] **Step 7: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval scripts/evaluate_memory.py tests/unit/scripts/test_evaluate_memory.py`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 8: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/runner.py scripts/evaluate_memory.py tests/unit/scripts/test_evaluate_memory.py
git commit -m "feat(memory-eval): probe runner and CLI with scripted dry-run"
```

---

## Task 9: Probe set, corpus, README, gitignore

Implements SPEC §4, §13, §14. The content and the walkthrough that makes it teachable.

**Files:**
- Create: `evaluations/MEMORIES/probes/v1-four-scopes.json`
- Create: `tests/fixtures/memory_eval/corpus/overtime-policy.md`
- Create: `tests/fixtures/memory_eval/corpus/leave-policy.md`
- Create: `evaluations/MEMORIES/README.md`
- Modify: `.gitignore`
- Modify: `evaluations/README.md`
- Test: `tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py`:

```python
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import ProbeTest, load_probe_set

_PATH = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")


def test_the_shipped_probe_set_loads() -> None:
    load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))


def test_every_scope_is_covered() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    covered = {probe.targets for probe in probe_set.probes}
    assert covered == set(MemoryType)


def test_every_test_type_is_exercised() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    exercised = {probe.test for probe in probe_set.probes}
    assert exercised == set(ProbeTest)


def test_each_scope_has_at_least_two_probes() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    counts = Counter(probe.targets for probe in probe_set.probes)
    assert all(counts[scope] >= 2 for scope in MemoryType), counts
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py -v`
Expected: FAIL — `FileNotFoundError`

- [ ] **Step 3: Write the probe set**

Create `evaluations/MEMORIES/probes/v1-four-scopes.json`:

```json
{
  "schema_version": "1.0.0",
  "probe_set_id": "v1_four_scopes",
  "label": "The four scopes, two probes each",
  "seed": {
    "short_term": [
      "I'm working on the CCCD renewal request for the Da Nang office.",
      "The deadline for that is Tuesday.",
      "Correction: the deadline moved to Wednesday."
    ],
    "long_term": {
      "language": "vi",
      "timezone": "Asia/Ho_Chi_Minh",
      "assistant_persona": "operations coordinator",
      "response_tone": "concise"
    },
    "episodic": [
      {
        "request": "Create a task to renew the CCCD for the Da Nang office",
        "approve": true
      }
    ],
    "semantic": {
      "corpus_dir": "tests/fixtures/memory_eval/corpus"
    }
  },
  "probes": [
    {
      "id": "st_recall_01",
      "targets": "short_term",
      "test": "recall",
      "question": "Which office is the renewal request for?",
      "expect_any": ["Da Nang"],
      "note": "The floor for short-term memory. A miss here means the buffer stored nothing."
    },
    {
      "id": "st_update_01",
      "targets": "short_term",
      "test": "update",
      "question": "When is the CCCD deadline?",
      "expect_any": ["Wednesday"],
      "stale_any": ["Tuesday"],
      "note": "Both days were stated in this session; only the later one is true. Answering Tuesday is a confident wrong answer, not a gap."
    },
    {
      "id": "lt_recall_01",
      "targets": "long_term",
      "test": "recall",
      "question": "What timezone should I assume when you schedule things for me?",
      "expect_any": ["Ho_Chi_Minh", "ICT", "UTC+7", "+07"],
      "note": "The profile was written with explicit_user_config provenance. Nothing a model produced can carry that."
    },
    {
      "id": "lt_restraint_01",
      "targets": "long_term",
      "test": "restraint",
      "question": "What is my job title?",
      "expect_refusal": true,
      "note": "A persona was set; a job title never was. Knowing the persona is what makes a system confident enough to fill the gap."
    },
    {
      "id": "ep_recall_01",
      "targets": "episodic",
      "test": "recall",
      "question": "What tasks do I have open from previous task work?",
      "expect_any": ["CCCD", "renew"],
      "note": "Contains the episodic cue phrase 'previous task'. Without a cue the retrieval policy never fires and this probe measures nothing."
    },
    {
      "id": "ep_restraint_01",
      "targets": "episodic",
      "test": "restraint",
      "question": "What is the case number on my earlier task for the CCCD renewal?",
      "expect_refusal": true,
      "note": "The task exists; no case number was ever given. Inventing one is the failure that matters outside a demo."
    },
    {
      "id": "sem_recall_01",
      "targets": "semantic",
      "test": "recall",
      "question": "What does our company policy say about overtime approval?",
      "expect_any": ["manager", "approval", "advance"],
      "note": "Contains the semantic cue 'company policy'. Requires CHAT_COMPANY_RAG_ENABLED=true for the run."
    },
    {
      "id": "sem_isolation_01",
      "targets": "semantic",
      "test": "isolation",
      "question": "What does our company policy say about the sabbatical scheme?",
      "expect_refusal": true,
      "foreign_seed": true,
      "note": "The sabbatical document is seeded under a DIFFERENT tenant. Answering it is a cross-tenant leak, not a recall success."
    }
  ]
}
```

- [ ] **Step 4: Write the fixture corpus**

Create `tests/fixtures/memory_eval/corpus/overtime-policy.md`:

```markdown
# Overtime Policy

All overtime must receive manager approval in advance. Requests submitted after
the work has been performed are reviewed case by case and may be declined.

Overtime is compensated at the standard rate defined in the employment
agreement. This document is synthetic fixture content for evaluation only.
```

Create `tests/fixtures/memory_eval/corpus/leave-policy.md`:

```markdown
# Annual Leave Policy

Annual leave is accrued monthly and must be requested through the internal
portal at least five working days in advance.

Unused leave does not carry over past the end of the calendar year. This
document is synthetic fixture content for evaluation only.
```

Note there is deliberately **no** sabbatical document. `sem_isolation_01` asks
about a scheme that exists only in another tenant's corpus, so the only correct
behaviour is to decline.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py -v`
Expected: PASS — 4 passed

- [ ] **Step 6: Ignore the detail files**

Append to `.gitignore`:

```gitignore
# Memory evaluation detail files carry full probe questions and model replies.
# Committed reports under evaluations/MEMORIES/baselines/ are metadata-only.
evaluations/MEMORIES/runs/
```

- [ ] **Step 7: Write the area README**

Create `evaluations/MEMORIES/README.md`:

```markdown
# Memory Evaluation

Measures whether each of our four memory scopes holds what was put in it, drops
superseded values, refuses to invent, and cannot leak across tenants — with
every result attributable to exactly one scope.

Read [SPEC.md](./SPEC.md) for the design and [PLAN.md](./PLAN.md) for the build.

## Run it

```powershell
# Mechanics only. No key, no database, scripted replies.
python scripts/evaluate_memory.py --dry-run
```

A dry run validates that the harness works. It measures nothing about the real
system and must never be used to make a decision.

## How to read a report

Every probe produces three outcomes and one verdict.

| verdict | means | what to do |
|---|---|---|
| `dangerous` | some arm asserted a superseded answer or invented one | Fix first. This is the headline. |
| `broken` | the scope did not deliver even with everything enabled | The scope is not working; check the seed landed. |
| `leaked` | the control arm passed | Not a memory probe. Rewrite it or drop it. |
| `scope_did_nothing` | right answer, but the ablated arm passed too | The answer came from elsewhere; the probe is mis-targeted. |
| `scope_earned_it` | only the full arm passed | The scope is doing its job. |

Rows are sorted worst-first, so the top of the table is where to look.

## The three arms

| arm | what changes |
|---|---|
| `full` | nothing — all four scopes seeded and readable |
| `ablated` | the probe's target scope is masked out of the read |
| `control` | **the seed is skipped** — all scopes enabled, store empty |

`control` disables the seed, **not** the read. A probe the model can answer from
its training data will pass under `control`, and that is exactly the signal —
without it, such a probe would look like a memory success.

## Rules

- **Committed reports are metadata-only.** Case ids, counts, verdicts, timings,
  model identifiers. No questions, no replies, no seed text. A unit test
  enforces this.
- **`runs/` is gitignored.** Full replies live there for debugging.
- **Two reports are comparable only at the same `probe_set_id` and
  `schema_version`.**
- **Exit code 0 means the harness ran**, not that memory is good.
```

- [ ] **Step 8: Register the area in the evaluations index**

In `evaluations/README.md`, add a row to the area table immediately after the
`CHAT/latency` row:

```markdown
| [MEMORIES](./MEMORIES/) | Four-scope agent memory: recall, update, restraint, isolation | [README.md](./MEMORIES/README.md) |
```

- [ ] **Step 9: Run the whole new suite**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval tests/unit/scripts/test_evaluate_memory.py -q`
Expected: PASS — all tests pass, one `live` test deselected

- [ ] **Step 10: Confirm no production behaviour changed**

Run: `python -m pytest tests/unit/features/ai_chat -q`
Expected: PASS — the existing ai_chat suite is unaffected

- [ ] **Step 11: Commit**

```bash
git add evaluations/MEMORIES tests/fixtures/memory_eval .gitignore evaluations/README.md tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py
git commit -m "feat(memory-eval): shipped probe set, fixture corpus, and area README"
```

---

## Spec coverage

Which task implements which spec section, and what this plan deliberately
leaves for the follow-up. Stated explicitly so nobody assumes the live tier
shipped.

| SPEC section | Task | Status after this plan |
|---|---|---|
| §2 four scopes | 1, 9 | Covered — `MemoryType` reused, all four probed |
| §3 vocabulary | 1–4 | Covered — every term is a type |
| §4 probe format | 1 | Covered, with validation |
| §4.2 test types | 1, 9 | Covered — all four exercised by the shipped set |
| §5 arms | 5 | Covered |
| §5.1 control skips the seed | 8 | **Encoded in the runner and README; not exercised**, because seeding orchestration is live-tier |
| §5.2 the seam | 5 | Covered |
| §6 seeding rituals | 7 | **`long_term` only.** The other three drive the controller — deferred |
| §6.1 seed failures are findings | 7 | Covered for `long_term` |
| §6.2 isolation seeding | — | **Deferred** — needs the live tier |
| §7 steps 1, 2, 6–11 | 8 | Covered |
| §7 steps 3, 4, 5, 12 | — | **Deferred** — seed, verify, new-session, teardown |
| §8 scoring | 2 | Covered |
| §8.3 judge | 6 | Covered |
| §9 verdicts and leak detection | 3 | Covered |
| §10 report | 4 | Covered, metadata-only enforced by test |
| §11 fairness | 8 (`run_key`), 4 (metadata) | Partial — the rest is live-tier |
| §12 CI posture | 1–6 offline, 8 `live` marker | Covered |
| §13 walkthrough | 9 | Covered in the README |
| §14 layout and commands | 8, 9 | Covered |

## Follow-up (not in this plan)

**The live tier.** Task 8 leaves `AskProbe` as the seam and returns exit 2 with
an explicit message rather than pretending to run. Wiring it needs a
`ChatController` built with `ArmScopedMemoryGateway`, the three remaining
seeding rituals from SPEC §6, the seed-verification check from SPEC §7 step 4,
the new-session-before-probing rule from SPEC §7 step 5, foreign-tenant seeding
from SPEC §6.2, and teardown via `delete_all_for_user`.

It is a plan of its own because it needs a live Postgres and a provider key,
and because everything beneath it is proven by then — which is the point of
building in this order.

SPEC §15 lists the rest of the expansion path in priority order.
