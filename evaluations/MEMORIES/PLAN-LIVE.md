# Memory Evaluation Harness — Live Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the shipped probe set through the real `ChatController`, a real Gemini model and a real PostgreSQL store, so every probe outcome describes the system users actually get.

**Architecture:** The offline tier (PLAN.md Tasks 1–9) already owns every judgment: probe loading, scoring, verdicts, reports, arm masking. This plan supplies only the missing `AskProbe` callable and the seeding that must happen before it. A `LiveEnvironment` resolves the three external dependencies once and turns each absence into a typed finding rather than a crash; `live_seeding` performs the three remaining rituals through their real authorization paths; `live_runner` sequences identity, per-arm seeding, verification, session policy and teardown.

**Tech Stack:** Python 3.11+, `psycopg`/`psycopg_pool` (`.[postgres]` extra), PostgreSQL 16, `GeminiChatReply`, `JinaEmbeddingAdapter`, existing `cowork_agent.domain.chat_contracts` types, pytest with the `live` marker.

**Spec:** [`evaluations/MEMORIES/SPEC.md`](./SPEC.md) §6, §6.1, §6.2, §7. Read it before Task 1. **Prior plan:** [`PLAN.md`](./PLAN.md) — Tasks 1–9, already shipped and committed; this plan consumes their interfaces and changes none of them.

## Global Constraints

- Branch: `feat/agent-tool`. All paths are relative to the worktree root `.worktrees/feat/agent-tool/`.
- **pytest pins this checkout's `src` automatically** (`tests/conftest.py`). You do **not** need `PYTHONPATH=src` for pytest. You **do** need it for any non-pytest command, including running `scripts/evaluate_memory.py` directly — the shared venv's editable install points at the main tree, so without it `import cowork_agent` resolves to `C:\WORK\EMAIL-AGENT-v1\src\cowork_agent` and the `memory_eval` package does not exist there.
- **Do not pass `-n 0` or `-p no:xdist`.** The suite fans out to 4 workers by default. Use `-n 0` only to debug a single failure.
- Python `>=3.11`; ruff `target-version = "py311"`, `line-length = 100`, lint rules `["E", "F", "I", "UP", "B"]`.
- `mypy --strict` passes on `src/`. Everything under `src/cowork_agent/features/ai_chat/memory_eval/` is in scope.
- Every module starts with `from __future__ import annotations`.
- Dataclasses are `@dataclass(frozen=True, slots=True)`.
- **No production file is modified.** `controller.py`, `retrieval_policy.py`, `memory_gateway.py`, the domain contracts, and everything under `persistence/` and `integrations/` are read-only. The only files this plan modifies are `scripts/evaluate_memory.py`, `evaluations/MEMORIES/probes/v1-four-scopes.json`, and `evaluations/MEMORIES/README.md`.
- **Reuse `MemoryType`** from `cowork_agent.domain.chat_contracts`. Do not define a parallel scope enum.
- **Never name a class `Test*`** — pytest collects those.
- `schema_version` stays `"1.0.0"` on probe sets and reports.
- **Committed reports stay metadata-only.** No probe question, no reply text, no seed text. The Task 4 test from PLAN.md already enforces this and must keep passing.
- Every test that needs Postgres, Gemini or Jina is marked `@pytest.mark.live` (registered in `pyproject.toml`, deselected by default). **Everything else in this plan is unit-testable with fakes and must be.**
- **A missing external dependency is a finding, never a crash** (SPEC §6.1). Probes targeting an unavailable scope are reported `unscorable`; the other scopes still run.
- Commit after every task using the message in that task's final step.

---

## Environment this plan needs

| Dependency | Env var | Absence behaviour |
|---|---|---|
| PostgreSQL 16 | `PG_TEST_URL`, else `DATABASE_URL` | `long_term` + `episodic` probes `unscorable` |
| Gemini | `GEMINI_API_KEY` / `GEMINI_API_KEY_<n>` | the run cannot proceed at all; exit 1 |
| Jina embeddings | `JINA_API_KEY` | `semantic` probes `unscorable` |

The Postgres default matches the persistence suite: `postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_mail_todo`.

---

## Findings that shaped this plan

Read these before Task 1; two of them contradict the spec, and the plan follows the code.

**1. The gateway method is `delete_all_memory()`, not `delete_all_for_user()`.** SPEC §7 step 12 names a method that does not exist on `MemoryGateway`. The real signature is `async def delete_all_memory(self) -> MemoryDeletionReport`, and it deletes the profile, all episodes, and the session buffer for the gateway's own scope. `delete_all_for_user` is the *episodic port's* method, called internally. Task 8 uses `delete_all_memory()`.

**2. The semantic store has no tenant partition, so SPEC §6.2's semantic isolation probe cannot be honest.** Verified: `KnowledgeChunk` has no tenant field; `allowed_chunk_indices` filters only `document_ids` / `years` / `months`; `turbovec_memory.py` contains no tenant reference; and `load_corpus(corpus_dir, *, tenant_id=None)` accepts `tenant_id` and never reads it. Company RAG is corpus-wide by design — `MemoryGateway.delete_all_memory` even documents that it "never" touches company RAG.

Seeding a foreign document into that store and asking about it would report `dangerous` on every run, describing the offline harness rather than a regression. Long-term and episodic isolation, by contrast, is real and enforced in SQL (`PostgresChatProfileRepository` keys on the namespace; `test_profiles_are_isolated_per_user` covers it) and in `MemoryGateway._require_scope` via `NamespaceAccessDenied`.

**Task 9 therefore retargets the isolation probe to `long_term`** and replaces `sem_isolation_01` with a semantic restraint probe. The semantic tenancy gap is recorded in the README as a known limitation, not laundered through a passing test.

**3. The episode id arrives on the event stream.** `ChatController.stream_message` emits a `MEMORY_CITATION` event with `memory_type is MemoryCitationType.EPISODIC` and `source_id` set to the new `episode_id`. Approval then goes through `controller.approve_task_episode(episode_id)` — the real user path — rather than a hand-built `gateway.transition_task_episode(...)` call as SPEC §6 sketches. Both reach `ValidationStatus.USER_APPROVED`; the controller route is the one a product action takes, which is the whole point of §6.

**3b. An episode needs BOTH an explicit request and a provider proposal.** Discovered while implementing Task 4. `is_explicit_task_request(request)` is necessary but not sufficient: `controller.py:725` writes the episode only when the reply provider also returned a `ChatTaskProposal`. With a plain-string reply the controller emits a `task_episode_unavailable` error event and no episode exists to approve.

`GeminiChatReply` does return proposals, gated on `task_proposal_requested` in the generation context, so episodic seeding works live. But a model that answers with `task_proposal: null` — which its own schema permits — produces the same empty result. `seed_episodic` reports this as a finding rather than letting it read as episodic amnesia, and `tests/.../test_live_seeding.py::test_a_provider_that_returns_no_proposal_is_a_finding` pins the behaviour. Any fake reply port used to test episodic seeding must yield a `ChatReplyChunk` carrying a proposal.

**4. `asyncio` needs a selector loop for psycopg on Windows.** The persistence suite runs every coroutine through `asyncio.run(scenario(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))`. The default `ProactorEventLoop` is unsupported by psycopg async. Task 1 centralises this.

---

## File Structure

| File | Responsibility | Needs a live dependency? |
|---|---|---|
| `src/cowork_agent/features/ai_chat/memory_eval/live_env.py` | `ScopeAvailability`, `LiveEnvironment`, dependency probing, selector-loop runner | no (probing is injectable) |
| `src/cowork_agent/features/ai_chat/memory_eval/live_controller.py` | `collect_reply()`, `build_arm_controller()`, `make_ask_probe()` | no (fake `ChatReplyPort`) |
| `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py` | `seed_short_term`, `seed_episodic`, `seed_semantic`, `verify_seed` | no (fakes) |
| `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py` | identity, per-arm sequencing, session policy, teardown, unscorable bookkeeping | no (fakes) |
| `scripts/evaluate_memory.py` | replace the live-tier `return 2` with the real path | yes, at runtime |
| `evaluations/MEMORIES/probes/v1-four-scopes.json` | retargeted isolation probe | no |
| `tests/integration/memory_eval/test_live_smoke.py` | one `@pytest.mark.live` end-to-end proof | yes |

Tasks 1–8 are unit-tested with fakes and run in the default suite. Task 10 is the only `live`-marked test.

---

## Task 1: Live environment and dependency probing

Implements SPEC §6.1. Turns three optional external dependencies into typed findings.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/live_env.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_live_env.py`

**Interfaces:**
- Consumes: `MemoryType` from `cowork_agent.domain.chat_contracts`.
- Produces: `ScopeAvailability(scope: MemoryType, available: bool, reason: str)`, `LiveEnvironment(postgres_url: str | None, gemini_ready: bool, jina_ready: bool)`, `probe_environment(environ, *, postgres_probe) -> LiveEnvironment`, `unavailable_scopes(env) -> tuple[ScopeAvailability, ...]`, `run_with_selector_loop(coro) -> object`, `POSTGRES_DEFAULT_URL`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_live_env.py`:

```python
from __future__ import annotations

import asyncio

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.live_env import (
    POSTGRES_DEFAULT_URL,
    probe_environment,
    run_with_selector_loop,
    unavailable_scopes,
)


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "PG_TEST_URL": "postgresql://x/y",
        "GEMINI_API_KEY": "k",
        "JINA_API_KEY": "j",
    }
    base.update(overrides)
    return base


def test_everything_present_reports_no_unavailable_scopes() -> None:
    env = probe_environment(_env(), postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://x/y"
    assert env.gemini_ready is True
    assert env.jina_ready is True
    assert unavailable_scopes(env) == ()


def test_pg_test_url_wins_over_database_url() -> None:
    env = probe_environment(
        _env(DATABASE_URL="postgresql://ignored/db"), postgres_probe=lambda url: True
    )
    assert env.postgres_url == "postgresql://x/y"


def test_database_url_is_used_when_pg_test_url_is_absent() -> None:
    environ = _env()
    del environ["PG_TEST_URL"]
    environ["DATABASE_URL"] = "postgresql://fallback/db"
    env = probe_environment(environ, postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://fallback/db"


def test_the_documented_default_is_used_when_neither_is_set() -> None:
    environ = _env()
    del environ["PG_TEST_URL"]
    seen: list[str] = []

    def probe(url: str) -> bool:
        seen.append(url)
        return True

    probe_environment(environ, postgres_probe=probe)
    assert seen == [POSTGRES_DEFAULT_URL]


def test_an_unreachable_server_makes_the_two_sql_scopes_unavailable() -> None:
    env = probe_environment(_env(), postgres_probe=lambda url: False)
    assert env.postgres_url is None
    scopes = {item.scope for item in unavailable_scopes(env)}
    assert scopes == {MemoryType.LONG_TERM, MemoryType.EPISODIC}


def test_a_missing_jina_key_makes_only_semantic_unavailable() -> None:
    environ = _env()
    del environ["JINA_API_KEY"]
    env = probe_environment(environ, postgres_probe=lambda url: True)
    unavailable = unavailable_scopes(env)
    assert [item.scope for item in unavailable] == [MemoryType.SEMANTIC]
    assert "JINA_API_KEY" in unavailable[0].reason


def test_short_term_is_never_unavailable() -> None:
    # The session buffer is in-process; nothing external can take it away.
    env = probe_environment({}, postgres_probe=lambda url: False)
    assert MemoryType.SHORT_TERM not in {item.scope for item in unavailable_scopes(env)}


def test_a_numbered_gemini_key_counts_as_ready() -> None:
    environ = _env()
    del environ["GEMINI_API_KEY"]
    environ["GEMINI_API_KEY_1"] = "k1"
    assert probe_environment(environ, postgres_probe=lambda url: True).gemini_ready is True


def test_run_with_selector_loop_returns_the_coroutine_result() -> None:
    async def work() -> str:
        await asyncio.sleep(0)
        return "done"

    assert run_with_selector_loop(work()) == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_env.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.live_env'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/live_env.py`:

```python
"""Resolving the live tier's external dependencies (SPEC §6.1).

Three things can be missing independently: a PostgreSQL server, a Gemini key,
a Jina key. Each absence disables a specific set of scopes and nothing else.

A harness that dies on the first missing dependency tells you nothing about the
other scopes, so every absence becomes a typed finding the report can carry.
Gemini is the one exception and it is handled by the caller: with no model
there is no reply to score, so there is no run at all.
"""

from __future__ import annotations

import asyncio
import selectors
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from cowork_agent.domain.chat_contracts import MemoryType

#: Matches tests/integration/persistence/*, so one running dev container serves both.
POSTGRES_DEFAULT_URL = "postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_mail_todo"

_CONNECT_TIMEOUT_SECONDS = 3

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ScopeAvailability:
    scope: MemoryType
    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class LiveEnvironment:
    postgres_url: str | None
    gemini_ready: bool
    jina_ready: bool


def default_postgres_probe(url: str) -> bool:
    """True when `url` accepts a connection. Mirrors tests/integration/persistence/pg_probe."""

    if not url:
        return False
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(url, connect_timeout=_CONNECT_TIMEOUT_SECONDS):
            return True
    except psycopg.Error:
        return False


def _gemini_ready(environ: Mapping[str, str]) -> bool:
    # GeminiSettings.from_env accepts GEMINI_API_KEY or numbered GEMINI_API_KEY_<n>.
    if environ.get("GEMINI_API_KEY"):
        return True
    return any(name.startswith("GEMINI_API_KEY_") and value for name, value in environ.items())


def probe_environment(
    environ: Mapping[str, str],
    *,
    postgres_probe: Callable[[str], bool] = default_postgres_probe,
) -> LiveEnvironment:
    """Resolve which external dependencies are actually usable right now."""

    url = environ.get("PG_TEST_URL") or environ.get("DATABASE_URL") or POSTGRES_DEFAULT_URL
    reachable = postgres_probe(url)
    return LiveEnvironment(
        postgres_url=url if reachable else None,
        gemini_ready=_gemini_ready(environ),
        jina_ready=bool(environ.get("JINA_API_KEY")),
    )


def unavailable_scopes(env: LiveEnvironment) -> tuple[ScopeAvailability, ...]:
    """Which scopes cannot be evaluated, and why, in report-ready form.

    short_term is never listed: the session buffer is in-process, so no external
    outage can remove it.
    """

    findings: list[ScopeAvailability] = []
    if env.postgres_url is None:
        reason = "no PostgreSQL server (set PG_TEST_URL or start cowork-pg)"
        findings.append(ScopeAvailability(MemoryType.LONG_TERM, False, reason))
        findings.append(ScopeAvailability(MemoryType.EPISODIC, False, reason))
    if not env.jina_ready:
        findings.append(
            ScopeAvailability(MemoryType.SEMANTIC, False, "no JINA_API_KEY; corpus cannot be embedded")
        )
    return tuple(findings)


def run_with_selector_loop(coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` on a selector loop.

    Windows defaults to ProactorEventLoop, which psycopg's async path does not
    support. The persistence suite does exactly this; the live tier must too or
    every database call fails on a developer machine.
    """

    return asyncio.run(
        coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_env.py -q`
Expected: PASS — 9 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_env.py tests/unit/features/ai_chat/memory_eval/test_live_env.py
git commit -m "feat(memory-eval): live dependency probing with per-scope findings"
```

---

## Task 2: Reply collection and the arm-scoped controller

Implements SPEC §7 step 6. Supplies the `AskProbe` callable PLAN.md Task 8 left as a seam.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/live_controller.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_live_controller.py`

**Interfaces:**
- Consumes: `Arm`, `ArmScopedMemoryGateway` (PLAN.md Task 5); `Probe` (Task 1); `ChatController`, `ChatMessageRequest`, `ChatEventType`, `ChatMemoryScope`.
- Produces: `collect_reply(events) -> tuple[str, tuple[str, ...]]`, `AdapterSet`, `build_arm_controller(scope, adapters, reply, *, masked_scope, company_rag_enabled) -> tuple[ChatController, ArmScopedMemoryGateway]`, `ask_once(controller, session_id, question, idempotency_key) -> tuple[str, int]`.

`collect_reply` returns the concatenated DELTA text and the episode ids seen on EPISODIC memory-citation events, because both are needed and iterating the stream twice is not possible.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_live_controller.py`:

```python
from __future__ import annotations

import asyncio

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatMemoryScope,
    ChatMessageStreamEvent,
    MemoryCitationType,
    MemoryType,
)
from cowork_agent.features.ai_chat.memory_eval.live_controller import (
    AdapterSet,
    ask_once,
    build_arm_controller,
    collect_reply,
)


class _Reply:
    """Minimal ChatReplyPort stand-in that streams a fixed sentence."""

    def __init__(self, text: str = "the answer is Wednesday") -> None:
        self._text = text
        self.prompts: list[object] = []

    async def stream_reply(self, request: object, context: object):  # noqa: ANN201 - structural
        del context
        self.prompts.append(request)
        yield self._text


def _event(event_type: ChatEventType, **kwargs: object) -> ChatMessageStreamEvent:
    return ChatMessageStreamEvent(
        event_id="e", session_id="s", turn_id="t", event_type=event_type, **kwargs
    )


def test_collect_reply_concatenates_only_delta_text() -> None:
    events = [
        _event(ChatEventType.STARTED),
        _event(ChatEventType.DELTA, text="Wed"),
        _event(ChatEventType.DELTA, text="nesday"),
        _event(ChatEventType.COMPLETED),
    ]
    text, episode_ids = collect_reply(events)
    assert text == "Wednesday"
    assert episode_ids == ()


def test_collect_reply_captures_episodic_citation_source_ids() -> None:
    events = [
        _event(ChatEventType.DELTA, text="ok"),
        _event(
            ChatEventType.MEMORY_CITATION,
            memory_type=MemoryCitationType.EPISODIC,
            source_id="ep-1",
        ),
    ]
    text, episode_ids = collect_reply(events)
    assert text == "ok"
    assert episode_ids == ("ep-1",)


def test_collect_reply_ignores_non_episodic_citations() -> None:
    events = [
        _event(
            ChatEventType.MEMORY_CITATION,
            memory_type=MemoryCitationType.LONG_TERM,
            source_id="prof-1",
        )
    ]
    assert collect_reply(events)[1] == ()


def test_collect_reply_of_an_empty_stream_is_empty_not_a_crash() -> None:
    assert collect_reply([]) == ("", ())


def test_build_arm_controller_masks_the_named_scope() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    _, gateway = build_arm_controller(
        scope, AdapterSet(), _Reply(), masked_scope=MemoryType.EPISODIC
    )
    assert gateway._masked_scope is MemoryType.EPISODIC


def test_build_arm_controller_masks_nothing_for_the_full_arm() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    _, gateway = build_arm_controller(scope, AdapterSet(), _Reply(), masked_scope=None)
    assert gateway._masked_scope is None


def test_ask_once_returns_the_reply_text_and_a_latency() -> None:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    controller, _ = build_arm_controller(scope, AdapterSet(), _Reply(), masked_scope=None)
    text, latency_ms = asyncio.run(ask_once(controller, "s", "which day?", "probe-1"))
    assert "Wednesday" in text
    assert latency_ms >= 0


def test_each_ask_uses_a_distinct_idempotency_key() -> None:
    # Reusing a key replays the cached turn instead of asking again, which would
    # make every arm after the first return the first arm's answer.
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    reply = _Reply()
    controller, _ = build_arm_controller(scope, AdapterSet(), reply, masked_scope=None)
    asyncio.run(ask_once(controller, "s", "q", "probe-1-full"))
    asyncio.run(ask_once(controller, "s", "q", "probe-1-control"))
    assert len(reply.prompts) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_controller.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.live_controller'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/live_controller.py`:

```python
"""Driving the real ChatController under one arm (SPEC §7 step 6).

PLAN.md Task 8 defined `AskProbe` and left the live implementation out. This is
it. Nothing here judges anything — it asks a question and returns text, and the
already-tested scoring layer decides what the text means.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from time import monotonic

from cowork_agent.domain.chat_contracts import (
    ChatEventType,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    MemoryCitationType,
    MemoryType,
)

from ..controller import ChatController
from ..session_buffer import InMemoryChatSessionBuffer
from .arms import ArmScopedMemoryGateway

#: Matches the production chat session buffer defaults.
_BUFFER_MAX_TURNS = 20
_BUFFER_TTL_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class AdapterSet:
    """The memory adapters a run has managed to build.

    Every field is optional because each one depends on an external service that
    may be absent. A gateway built without an adapter fails closed for that
    scope, which is exactly what an unavailable scope should look like.
    """

    declarative_memory: object | None = None
    episodic_memory: object | None = None
    semantic_memory: object | None = None


def collect_reply(
    events: Iterable[ChatMessageStreamEvent],
) -> tuple[str, tuple[str, ...]]:
    """Reduce one event stream to its reply text and any episode ids it cited.

    Both are read in a single pass because the stream is consumed once. The
    episode ids are how episodic seeding learns what to approve (SPEC §6).
    """

    chunks: list[str] = []
    episode_ids: list[str] = []
    for event in events:
        if event.event_type is ChatEventType.DELTA and event.text:
            chunks.append(event.text)
        elif (
            event.event_type is ChatEventType.MEMORY_CITATION
            and event.memory_type is MemoryCitationType.EPISODIC
            and event.source_id
        ):
            episode_ids.append(event.source_id)
    return "".join(chunks), tuple(episode_ids)


def build_arm_controller(
    scope: ChatMemoryScope,
    adapters: AdapterSet,
    reply: object,
    *,
    masked_scope: MemoryType | None,
    company_rag_enabled: bool = True,
) -> tuple[ChatController, ArmScopedMemoryGateway]:
    """Build a controller whose gateway reports `masked_scope` as unavailable.

    The gateway is returned alongside the controller because seeding, seed
    verification and teardown all address it directly.
    """

    gateway = ArmScopedMemoryGateway(
        masked_scope=masked_scope,
        scope=scope,
        session_buffer=InMemoryChatSessionBuffer(
            max_turns=_BUFFER_MAX_TURNS, ttl_seconds=_BUFFER_TTL_SECONDS
        ),
        declarative_memory=adapters.declarative_memory,  # type: ignore[arg-type]
        episodic_memory=adapters.episodic_memory,  # type: ignore[arg-type]
        semantic_memory=adapters.semantic_memory,  # type: ignore[arg-type]
    )
    controller = ChatController(
        scope=scope,
        memory=gateway,
        reply=reply,  # type: ignore[arg-type]
        company_rag_enabled=company_rag_enabled,
    )
    return controller, gateway


async def ask_once(
    controller: ChatController,
    session_id: str,
    question: str,
    idempotency_key: str,
) -> tuple[str, int]:
    """Ask one question and return (reply text, latency in ms).

    `idempotency_key` must be unique per (probe, arm). The controller caches a
    completed turn by that key and replays it verbatim, so a shared key would
    hand every later arm the first arm's answer and silently produce a run in
    which ablation never changed anything.
    """

    request = ChatMessageRequest(session_id, question, idempotency_key)
    started = monotonic()
    events = [event async for event in controller.stream_message(request)]
    latency_ms = int((monotonic() - started) * 1000)
    text, _ = collect_reply(events)
    return text, latency_ms


def episode_ids_from(events: Sequence[ChatMessageStreamEvent]) -> tuple[str, ...]:
    """The episodic citation ids in `events`. Used by episodic seeding."""

    return collect_reply(events)[1]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_controller.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_controller.py tests/unit/features/ai_chat/memory_eval/test_live_controller.py
git commit -m "feat(memory-eval): arm-scoped controller and reply collection"
```

---

## Task 3: Short-term seeding

Implements SPEC §6, `short_term` ritual. The buffer is filled by talking, exactly as a user fills it.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_live_seeding.py`

**Interfaces:**
- Consumes: `SeedSpec` (PLAN.md Task 1), `SeedOutcome` (PLAN.md Task 7), `ask_once` (Task 2).
- Produces: `async def seed_short_term(controller, session_id, spec, *, key_prefix) -> SeedOutcome`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_live_seeding.py`:

```python
from __future__ import annotations

import asyncio

from cowork_agent.domain.chat_contracts import ChatMemoryScope, MemoryType
from cowork_agent.features.ai_chat.memory_eval.live_controller import (
    AdapterSet,
    build_arm_controller,
)
from cowork_agent.features.ai_chat.memory_eval.live_seeding import seed_short_term
from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec


class _Reply:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    async def stream_reply(self, request: object, context: object):  # noqa: ANN201 - structural
        del request, context
        self.calls += 1
        if self._fail:
            raise RuntimeError("model down")
        yield "acknowledged"


def _controller(reply: object):  # noqa: ANN201 - returns a controller pair
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    return build_arm_controller(scope, AdapterSet(), reply, masked_scope=None)


def test_each_seed_line_is_sent_as_its_own_turn() -> None:
    reply = _Reply()
    controller, _ = _controller(reply)
    spec = SeedSpec(("line one", "line two", "line three"), {}, (), None)
    outcome = asyncio.run(seed_short_term(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is True
    assert outcome.scope is MemoryType.SHORT_TERM
    assert reply.calls == 3


def test_nothing_declared_is_a_skip_not_a_failure() -> None:
    controller, _ = _controller(_Reply())
    outcome = asyncio.run(seed_short_term(controller, "s", SeedSpec((), {}, (), None), key_prefix="seed"))
    assert outcome.ok is True
    assert outcome.reason == "nothing declared"


def test_a_model_failure_is_reported_as_a_finding() -> None:
    controller, _ = _controller(_Reply(fail=True))
    spec = SeedSpec(("line one",), {}, (), None)
    outcome = asyncio.run(seed_short_term(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is False
    assert "model down" in outcome.reason


def test_the_buffer_holds_every_seeded_turn() -> None:
    controller, gateway = _controller(_Reply())
    spec = SeedSpec(("alpha", "beta"), {}, (), None)
    asyncio.run(seed_short_term(controller, "s", spec, key_prefix="seed"))
    turns = gateway._read_active_turns()
    assert any("alpha" in (turn.user_message or "") for turn in turns)
    assert any("beta" in (turn.user_message or "") for turn in turns)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.live_seeding'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`:

```python
"""The three controller-driven seeding rituals (SPEC §6).

`long_term` already ships in `seeding.py` — it needs only the gateway. These
three need the controller, because the authorization step under test lives
there: a turn must be spoken to enter the buffer, and a task must be requested
and approved to become retrievable.

Every ritual returns a SeedOutcome instead of raising. A scope that fails to
seed is a finding about that scope (SPEC §6.1), and the other three still run.
"""

from __future__ import annotations

from cowork_agent.domain.chat_contracts import MemoryType

from ..controller import ChatController
from .live_controller import ask_once
from .probes import SeedSpec
from .seeding import SeedOutcome


async def seed_short_term(
    controller: ChatController,
    session_id: str,
    spec: SeedSpec,
    *,
    key_prefix: str,
) -> SeedOutcome:
    """Speak each declared line as its own turn so the buffer fills naturally.

    Writing turns straight into the buffer would skip `stream_message`, which is
    what actually decides a turn is worth keeping. It would also make the probe
    pass on a buffer state no conversation can produce.
    """

    if not spec.short_term:
        return SeedOutcome(MemoryType.SHORT_TERM, True, "nothing declared")
    try:
        for index, line in enumerate(spec.short_term):
            await ask_once(controller, session_id, line, f"{key_prefix}-st-{index}")
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return SeedOutcome(MemoryType.SHORT_TERM, False, f"{type(error).__name__}: {error}")
    return SeedOutcome(MemoryType.SHORT_TERM, True, f"seeded {len(spec.short_term)} turns")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py -q`
Expected: PASS — 4 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py tests/unit/features/ai_chat/memory_eval/test_live_seeding.py
git commit -m "feat(memory-eval): short-term seeding through real chat turns"
```

---

## Task 4: Episodic seeding through request and approval

Implements SPEC §6, `episodic` ritual. The two-step authorization that makes an episode retrievable.

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_live_seeding.py` (append)

**Interfaces:**
- Consumes: `EpisodeSeed`, `SeedSpec` (PLAN.md Task 1); `collect_reply` (Task 2); `ChatController.approve_task_episode`.
- Produces: `async def seed_episodic(controller, session_id, spec, *, key_prefix) -> SeedOutcome`.

**Why approval goes through the controller.** A freshly written episode is `SYSTEM_GENERATED` with `retrieval_eligible=false`, so it is deliberately unreadable. `controller.approve_task_episode(episode_id)` transitions it to `USER_APPROVED` — the same call the product makes when a user accepts a task proposal. Building the transition by hand against the gateway would reach the same row while skipping the path under test.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/features/ai_chat/memory_eval/test_live_seeding.py`:

```python
from cowork_agent.domain.chat_contracts import TaskEpisode, ValidationStatus
from cowork_agent.features.ai_chat.memory_eval.live_seeding import seed_episodic
from cowork_agent.features.ai_chat.memory_eval.probes import EpisodeSeed


class _EpisodicStore:
    """Namespace-keyed episodic store, enough to exercise the lifecycle."""

    def __init__(self) -> None:
        self.episodes: dict[str, TaskEpisode] = {}
        self.transitions: list[tuple[str, ValidationStatus]] = []

    async def write_task_episode(
        self, namespace: object, episode: TaskEpisode, *, expires_at: object
    ) -> TaskEpisode:
        del namespace, expires_at
        self.episodes[episode.episode_id] = episode
        return episode

    async def read_task_episode(self, namespace: object, *, episode_id: str) -> TaskEpisode | None:
        del namespace
        return self.episodes.get(episode_id)

    async def transition_task_episode(self, transition: object) -> TaskEpisode | None:
        episode_id = getattr(transition, "episode_id", "")
        to_status = getattr(transition, "to_status", None)
        self.transitions.append((episode_id, to_status))
        return self.episodes.get(episode_id)

    async def read_eligible_task_episodes(self, namespace: object, query: object) -> tuple[TaskEpisode, ...]:
        del namespace, query
        return tuple(self.episodes.values())


def _episodic_controller(store: _EpisodicStore):  # noqa: ANN201 - returns a controller pair
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    return build_arm_controller(
        scope, AdapterSet(episodic_memory=store), _Reply(), masked_scope=None
    )


def test_an_approved_seed_is_transitioned_to_user_approved() -> None:
    store = _EpisodicStore()
    controller, _ = _episodic_controller(store)
    spec = SeedSpec(
        (), {}, (EpisodeSeed(request="Create a task to renew the CCCD", approve=True),), None
    )
    outcome = asyncio.run(seed_episodic(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is True
    assert ValidationStatus.USER_APPROVED in [status for _, status in store.transitions]


def test_an_unapproved_seed_is_written_but_never_transitioned() -> None:
    # retrieval_eligible stays false. That is a valid thing to seed: it is how
    # the eligibility gate itself gets tested.
    store = _EpisodicStore()
    controller, _ = _episodic_controller(store)
    spec = SeedSpec(
        (), {}, (EpisodeSeed(request="Create a task to renew the CCCD", approve=False),), None
    )
    outcome = asyncio.run(seed_episodic(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is True
    assert store.transitions == []


def test_a_request_the_policy_rejects_is_a_finding_naming_the_phrasing() -> None:
    # is_explicit_task_request refuses this phrasing, so no episode is created.
    # That is a finding about the authorization policy, not a crash.
    store = _EpisodicStore()
    controller, _ = _episodic_controller(store)
    spec = SeedSpec((), {}, (EpisodeSeed(request="what is the weather", approve=True),), None)
    outcome = asyncio.run(seed_episodic(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is False
    assert "no task episode" in outcome.reason


def test_nothing_declared_is_a_skip() -> None:
    store = _EpisodicStore()
    controller, _ = _episodic_controller(store)
    outcome = asyncio.run(
        seed_episodic(controller, "s", SeedSpec((), {}, (), None), key_prefix="seed")
    )
    assert outcome.ok is True
    assert outcome.reason == "nothing declared"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py -q`
Expected: FAIL — `ImportError: cannot import name 'seed_episodic'`

- [ ] **Step 3: Write the implementation**

Append to `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`:

```python
async def seed_episodic(
    controller: ChatController,
    session_id: str,
    spec: SeedSpec,
    *,
    key_prefix: str,
) -> SeedOutcome:
    """Request each task, then approve it if the seed says so.

    Two steps, because our system takes two. `stream_message` writes the episode
    SYSTEM_GENERATED with retrieval_eligible=false; only the approval makes it
    readable. Seeding just the first step and probing for recall would report
    amnesia that is actually the eligibility gate working correctly.

    A request the phrasing policy rejects produces no episode at all. That is a
    finding about `is_explicit_task_request`, reported as one.
    """

    if not spec.episodic:
        return SeedOutcome(MemoryType.EPISODIC, True, "nothing declared")

    approved = 0
    try:
        for index, entry in enumerate(spec.episodic):
            request = ChatMessageRequest(
                session_id, entry.request, f"{key_prefix}-ep-{index}"
            )
            events = [event async for event in controller.stream_message(request)]
            _, episode_ids = collect_reply(events)
            if not episode_ids:
                return SeedOutcome(
                    MemoryType.EPISODIC,
                    False,
                    f"no task episode created for seed {index}; "
                    "is_explicit_task_request rejected the phrasing",
                )
            if entry.approve:
                for episode_id in episode_ids:
                    await controller.approve_task_episode(episode_id)
                    approved += 1
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return SeedOutcome(MemoryType.EPISODIC, False, f"{type(error).__name__}: {error}")
    return SeedOutcome(
        MemoryType.EPISODIC, True, f"seeded {len(spec.episodic)} episodes, approved {approved}"
    )
```

Add `ChatMessageRequest` and `collect_reply` to the module's imports:

```python
from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryType

from ..controller import ChatController
from .live_controller import ask_once, collect_reply
from .probes import SeedSpec
from .seeding import SeedOutcome
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py tests/unit/features/ai_chat/memory_eval/test_live_seeding.py
git commit -m "feat(memory-eval): episodic seeding through request and approval"
```

---

## Task 5: Semantic corpus indexing

Implements SPEC §6, `semantic` ritual. The corpus is indexed once and read through the same adapter production uses.

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_live_seeding.py` (append)

**Interfaces:**
- Consumes: `SeedSpec` (PLAN.md Task 1); `load_corpus` from `cowork_agent.integrations.rag.knowledge_base`; `InRepoSemanticMemory` from `cowork_agent.integrations.rag.memory`; `SemanticChatMemoryAdapter` from `cowork_agent.integrations.rag.chat_memory`.
- Produces: `async def seed_semantic(spec, embedder, *, corpus_root) -> tuple[SeedOutcome, object | None]` returning the outcome and the built `SemanticChatMemoryAdapter` (or `None`).

**Why `InRepoSemanticMemory`.** Its own docstring says it "stays only for offline evaluation harnesses" — this is that harness. It re-embeds the corpus per process, which is acceptable for eight probes and gives a deterministic index with no external vector store. It emits a `DeprecationWarning` by design; the implementation suppresses it locally with a comment rather than letting a warning filter turn a run into an error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/features/ai_chat/memory_eval/test_live_seeding.py`:

```python
from pathlib import Path

from cowork_agent.features.ai_chat.memory_eval.live_seeding import seed_semantic

_CORPUS = "tests/fixtures/memory_eval/corpus"


class _Embedder:
    """Deterministic bag-of-words embedder. No network, stable across runs."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def embed(self, texts: tuple[str, ...], *, task: str = "") -> list[list[float]]:
        del task
        if self._fail:
            raise RuntimeError("embedder down")
        vocabulary = ("overtime", "manager", "approval", "leave", "portal", "annual")
        return [
            [float(text.casefold().count(word)) + 1.0 for word in vocabulary] for text in texts
        ]


def test_a_declared_corpus_is_indexed_and_an_adapter_returned() -> None:
    spec = SeedSpec((), {}, (), _CORPUS)
    outcome, adapter = asyncio.run(seed_semantic(spec, _Embedder(), corpus_root=Path(".")))
    assert outcome.ok is True
    assert outcome.scope is MemoryType.SEMANTIC
    assert adapter is not None


def test_no_corpus_declared_is_a_skip_with_no_adapter() -> None:
    outcome, adapter = asyncio.run(
        seed_semantic(SeedSpec((), {}, (), None), _Embedder(), corpus_root=Path("."))
    )
    assert outcome.ok is True
    assert outcome.reason == "nothing declared"
    assert adapter is None


def test_a_missing_corpus_directory_is_a_finding() -> None:
    spec = SeedSpec((), {}, (), "tests/fixtures/memory_eval/does-not-exist")
    outcome, adapter = asyncio.run(seed_semantic(spec, _Embedder(), corpus_root=Path(".")))
    assert outcome.ok is False
    assert "corpus" in outcome.reason.casefold()
    assert adapter is None


def test_an_embedder_failure_is_a_finding_not_a_crash() -> None:
    spec = SeedSpec((), {}, (), _CORPUS)
    outcome, adapter = asyncio.run(
        seed_semantic(spec, _Embedder(fail=True), corpus_root=Path("."))
    )
    assert outcome.ok is False
    assert "embedder down" in outcome.reason
    assert adapter is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py -q`
Expected: FAIL — `ImportError: cannot import name 'seed_semantic'`

- [ ] **Step 3: Write the implementation**

Append to `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`:

```python
async def seed_semantic(
    spec: SeedSpec,
    embedder: object,
    *,
    corpus_root: Path,
) -> tuple[SeedOutcome, object | None]:
    """Index the declared corpus and return a read adapter for it.

    Semantic memory has no write path through the gateway — it is retrieval-only
    over a corpus someone else publishes. "Seeding" it therefore means building
    the index the read will hit, which is why this returns an adapter instead of
    mutating a store.

    The probe questions must carry a cue phrase such as "company policy" or the
    retrieval policy never fires and the probe measures nothing (SPEC §6).
    """

    if not spec.semantic_corpus_dir:
        return SeedOutcome(MemoryType.SEMANTIC, True, "nothing declared"), None
    try:
        documents = load_corpus(corpus_root / spec.semantic_corpus_dir)
        with warnings.catch_warnings():
            # InRepoSemanticMemory is deprecated for production and retained
            # explicitly for offline evaluation harnesses. This is one.
            warnings.simplefilter("ignore", DeprecationWarning)
            index = InRepoSemanticMemory(documents, embedder)  # type: ignore[arg-type]
        await index.build_index()
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return (
            SeedOutcome(MemoryType.SEMANTIC, False, f"{type(error).__name__}: {error}"),
            None,
        )
    return (
        SeedOutcome(MemoryType.SEMANTIC, True, f"indexed {len(documents)} documents"),
        SemanticChatMemoryAdapter(index),
    )
```

Final import block for the module:

```python
from __future__ import annotations

import warnings
from pathlib import Path

from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryType
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.memory import InRepoSemanticMemory

from ..controller import ChatController
from .live_controller import ask_once, collect_reply
from .probes import SeedSpec
from .seeding import SeedOutcome
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_seeding.py -q`
Expected: PASS — 12 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py tests/unit/features/ai_chat/memory_eval/test_live_seeding.py
git commit -m "feat(memory-eval): semantic corpus indexing for the live tier"
```

---

## Task 6: Seed verification

Implements SPEC §7 step 4. Our answer to waku's `settle()` — a check, not a wait.

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_seed_verification.py`

**Interfaces:**
- Consumes: `MemoryGateway.read_context`; `MemoryContextRequest`, `MemoryReadOptions`, `EpisodicMemoryQuery`, `SemanticMemoryQuery` from `cowork_agent.domain.chat_contracts`.
- Produces: `async def verify_seed(gateway, scope, expected_scopes) -> tuple[ScopeAvailability, ...]`.

**Why this exists.** A scope that silently failed to seed reports as amnesia, and amnesia is indistinguishable from a broken memory system in the report. One unmasked read after seeding turns that ambiguity into a named finding. Our writes are transactional, so no polling is needed — that is why this is a check rather than waku's retry loop.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_seed_verification.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatTurn,
    DeclarativeProfile,
    MemoryProvenanceSource,
    MemoryType,
)
from cowork_agent.features.ai_chat.memory_eval.live_seeding import verify_seed
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from datetime import UTC, datetime


class _Declarative:
    def __init__(self, profile: DeclarativeProfile | None) -> None:
        self._profile = profile

    async def read_profile(self, namespace: object) -> DeclarativeProfile | None:
        del namespace
        return self._profile

    async def write_profile(self, namespace: object, profile: DeclarativeProfile) -> DeclarativeProfile:
        del namespace
        return profile

    async def delete_profile(self, namespace: object) -> bool:
        del namespace
        return True


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")


def _profile() -> DeclarativeProfile:
    now = datetime(2026, 8, 18, tzinfo=UTC)
    return DeclarativeProfile(
        profile_id="p1",
        user_id="u",
        language="vi",
        timezone=None,
        assistant_persona=None,
        response_tone=None,
        created_at=now,
        updated_at=now,
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
    )


def test_a_seeded_profile_verifies(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory(declarative_memory=_Declarative(_profile()))
    findings = asyncio.run(verify_seed(gateway, _scope(), (MemoryType.LONG_TERM,)))
    assert findings == ()


def test_an_empty_profile_is_reported_as_a_seed_that_did_not_land(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory(declarative_memory=_Declarative(None))
    findings = asyncio.run(verify_seed(gateway, _scope(), (MemoryType.LONG_TERM,)))
    assert [item.scope for item in findings] == [MemoryType.LONG_TERM]
    assert findings[0].available is False
    assert "did not land" in findings[0].reason


def test_an_empty_short_term_buffer_is_reported(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory()
    findings = asyncio.run(verify_seed(gateway, _scope(), (MemoryType.SHORT_TERM,)))
    assert [item.scope for item in findings] == [MemoryType.SHORT_TERM]


def test_a_populated_buffer_verifies(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    gateway = memory_gateway_factory()
    gateway.append_turn(
        ChatTurn(
            turn_id="t1",
            session_id="s",
            user_message="a seeded line",
            assistant_message="ok",
            created_at=datetime(2026, 8, 18, tzinfo=UTC),
        )
    )
    assert asyncio.run(verify_seed(gateway, _scope(), (MemoryType.SHORT_TERM,))) == ()


def test_only_the_requested_scopes_are_checked(
    memory_gateway_factory: Callable[..., MemoryGateway],
) -> None:
    # An unseeded scope is not a failure — it was never declared.
    gateway = memory_gateway_factory(declarative_memory=_Declarative(None))
    assert asyncio.run(verify_seed(gateway, _scope(), ())) == ()
```

`ChatTurn` is `(turn_id, session_id, user_message, assistant_message, created_at, ...)` — verified at `src/cowork_agent/domain/_chat_contracts_memory.py:996`. `status` defaults to `ChatTurnStatus.COMPLETED`, so the test above does not pass it and does not import it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_seed_verification.py -q`
Expected: FAIL — `ImportError: cannot import name 'verify_seed'`

- [ ] **Step 3: Write the implementation**

Append to `src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py`:

```python
def _verification_reads() -> MemoryReadOptions:
    """Read every scope, masked by nothing. Verification must see the truth."""

    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=EpisodicMemoryQuery(
            query="previous task", max_items=5, min_score=0.0, timeout_ms=2000
        ),
        semantic=SemanticMemoryQuery(
            query="company policy", max_items=5, min_score=0.0, timeout_ms=2000
        ),
    )


async def verify_seed(
    gateway: MemoryGateway,
    scope: ChatMemoryScope,
    expected_scopes: Sequence[MemoryType],
) -> tuple[ScopeAvailability, ...]:
    """Confirm each seeded scope actually reads back non-empty.

    Called on the `full` and `<target>_off` arms only. The `control` arm has no
    seed by definition, so verifying it would fail every scope every run.

    Our writes are transactional, so this is a single check rather than a poll.
    """

    if not expected_scopes:
        return ()
    request = MemoryContextRequest(
        session_id=scope.session_id, scope=scope, reads=_verification_reads()
    )
    try:
        response = await gateway.read_context(request)
    except Exception as error:  # noqa: BLE001 - an unreadable store is a finding
        return tuple(
            ScopeAvailability(item, False, f"verification read failed: {error}")
            for item in expected_scopes
        )

    populated = {
        MemoryType.SHORT_TERM: bool(response.turns),
        MemoryType.LONG_TERM: response.profile is not None,
        MemoryType.EPISODIC: bool(response.episodes),
        MemoryType.SEMANTIC: response.semantic_context is not None,
    }
    return tuple(
        ScopeAvailability(item, False, f"{item.value} seed did not land: read back empty")
        for item in expected_scopes
        if not populated[item]
    )
```

Extend the module imports:

```python
from collections.abc import Sequence

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    EpisodicMemoryQuery,
    MemoryContextRequest,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryQuery,
)

from ..memory_gateway import MemoryGateway
from .live_env import ScopeAvailability
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_seed_verification.py -q`
Expected: PASS — 5 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_seeding.py tests/unit/features/ai_chat/memory_eval/test_seed_verification.py
git commit -m "feat(memory-eval): seed verification so a failed seed is not read as amnesia"
```

---

## Task 7: Run identity and the session policy

Implements SPEC §7 steps 1 and 5. The two rules that stop the harness measuring the wrong thing.

**Files:**
- Create: `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_live_runner.py`

**Interfaces:**
- Consumes: `run_key` (PLAN.md Task 8); `Probe`, `ProbeSet` (PLAN.md Task 1); `Arm` (PLAN.md Task 5); `MemoryType`.
- Produces: `RunIdentity(run_key, tenant_id, user_id, foreign_tenant_id, foreign_user_id)`, `build_identity(probe_set, model) -> RunIdentity`, `session_id_for(identity, probe, arm) -> str`, `needs_fresh_session(probe) -> bool`.

**The session rule.** The buffer feeds recent turns straight into the prompt. Seed three short-term turns, probe immediately, and the answer is sitting in the context window — the scope under test was never consulted. So every probe gets a fresh session **except** a `short_term` probe, where the buffer *is* the thing under test and must be kept.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/features/ai_chat/memory_eval/test_live_runner.py`:

```python
from __future__ import annotations

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    build_identity,
    needs_fresh_session,
    session_id_for,
)
from cowork_agent.features.ai_chat.memory_eval.probes import (
    Probe,
    ProbeSet,
    ProbeTest,
    SeedSpec,
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


def _probe_set() -> ProbeSet:
    return ProbeSet("1.0.0", "unit", "unit", SeedSpec(("a",), {}, (), None), (_probe(),))


def test_identity_is_namespaced_by_the_run_key() -> None:
    identity = build_identity(_probe_set(), "model-a")
    assert identity.tenant_id == f"memeval-{identity.run_key}"
    assert identity.user_id == f"memeval-{identity.run_key}"


def test_identity_is_stable_for_the_same_inputs() -> None:
    assert build_identity(_probe_set(), "m").run_key == build_identity(_probe_set(), "m").run_key


def test_a_different_model_gets_a_different_tenant() -> None:
    assert build_identity(_probe_set(), "m1").tenant_id != build_identity(_probe_set(), "m2").tenant_id


def test_the_foreign_identity_differs_from_the_primary() -> None:
    # An isolation probe is meaningless if both identities collide.
    identity = build_identity(_probe_set(), "m")
    assert identity.foreign_tenant_id != identity.tenant_id
    assert identity.foreign_user_id != identity.user_id


def test_a_short_term_probe_keeps_its_session() -> None:
    assert needs_fresh_session(_probe(targets=MemoryType.SHORT_TERM)) is False


def test_every_other_scope_gets_a_fresh_session() -> None:
    for scope in (MemoryType.LONG_TERM, MemoryType.EPISODIC, MemoryType.SEMANTIC):
        assert needs_fresh_session(_probe(targets=scope)) is True


def test_a_short_term_probe_reuses_the_seeded_session_id_per_arm() -> None:
    identity = build_identity(_probe_set(), "m")
    probe = _probe(targets=MemoryType.SHORT_TERM, probe_id="st_1")
    assert session_id_for(identity, probe, Arm.FULL) == session_id_for(identity, probe, Arm.FULL)


def test_session_ids_differ_across_arms() -> None:
    # Sharing a session across arms would leak the full arm's turns into control.
    identity = build_identity(_probe_set(), "m")
    probe = _probe(probe_id="ep_1")
    assert session_id_for(identity, probe, Arm.FULL) != session_id_for(identity, probe, Arm.CONTROL)


def test_session_ids_differ_across_probes() -> None:
    identity = build_identity(_probe_set(), "m")
    first = session_id_for(identity, _probe(probe_id="a"), Arm.FULL)
    second = session_id_for(identity, _probe(probe_id="b"), Arm.FULL)
    assert first != second
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '...memory_eval.live_runner'`

- [ ] **Step 3: Write the implementation**

Create `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py`:

```python
"""Live-tier orchestration: identity, session policy, teardown (SPEC §7).

Every rule here exists because breaking it makes the harness measure something
other than memory, and each one names what it prevents.
"""

from __future__ import annotations

from dataclasses import dataclass

from cowork_agent.domain.chat_contracts import MemoryType

from .arms import Arm
from .probes import Probe, ProbeSet
from .runner import run_key


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Throwaway identities for one run, plus the foreign one isolation uses."""

    run_key: str
    tenant_id: str
    user_id: str
    foreign_tenant_id: str
    foreign_user_id: str


def build_identity(probe_set: ProbeSet, model: str) -> RunIdentity:
    """Derive namespaced identities from the probe set, model and seed.

    Two properties matter. A run can never collide with another run or touch a
    real user's memory. And because the seed is part of the key, changing the
    seed addresses a different tenant — so a run can never quietly probe a store
    that was seeded for a different question.
    """

    key = run_key(probe_set.probe_set_id, model, probe_set.seed)
    return RunIdentity(
        run_key=key,
        tenant_id=f"memeval-{key}",
        user_id=f"memeval-{key}",
        foreign_tenant_id=f"memeval-foreign-{key}",
        foreign_user_id=f"memeval-foreign-{key}",
    )


def needs_fresh_session(probe: Probe) -> bool:
    """Whether this probe must be asked in a session that has no seeded turns.

    The buffer feeds recent turns into the prompt. For any scope other than
    short_term, probing in the seeded session puts the answer in the context
    window and the scope under test is never consulted — the probe would pass
    while proving nothing. For a short_term probe the buffer IS the subject, so
    the session is deliberately kept.
    """

    return probe.targets is not MemoryType.SHORT_TERM


def session_id_for(identity: RunIdentity, probe: Probe, arm: Arm) -> str:
    """A session id unique per (run, probe, arm).

    Arms never share a session. Sharing one would carry the full arm's turns
    into the control arm, and control would stop being a clean-store baseline.
    """

    return f"memeval-{identity.run_key}-{probe.probe_id}-{arm.value}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py -q`
Expected: PASS — 9 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_runner.py tests/unit/features/ai_chat/memory_eval/test_live_runner.py
git commit -m "feat(memory-eval): run identity and fresh-session policy"
```

---

## Task 8: Live orchestration and teardown

Implements SPEC §7 steps 2–3, 6, 12. Assembles Tasks 1–7 into the `AskProbe` PLAN.md Task 8 expects.

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py`
- Test: `tests/unit/features/ai_chat/memory_eval/test_live_runner.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–7; `run_probe_set` (PLAN.md Task 8); `SeedOutcome` (PLAN.md Task 7).
- Produces: `LiveSession` (holds adapters + identity + reply port), `async def ask_live(session, probe, arm, masked) -> tuple[str, int]`, `async def teardown(gateways) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/features/ai_chat/memory_eval/test_live_runner.py`:

```python
import asyncio

from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    LiveSession,
    ask_live,
    teardown,
)


class _Reply:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def stream_reply(self, request: object, context: object):  # noqa: ANN201 - structural
        del context
        self.questions.append(str(getattr(request, "user_message", "")))
        yield "an answer"


class _Gateway:
    def __init__(self, fail: bool = False) -> None:
        self.deleted = 0
        self._fail = fail

    async def delete_all_memory(self) -> object:
        if self._fail:
            raise RuntimeError("store gone")
        self.deleted += 1
        return object()


def _session(reply: object) -> LiveSession:
    return LiveSession(
        identity=build_identity(_probe_set(), "m"),
        adapters=AdapterSet(),
        reply=reply,
        company_rag_enabled=True,
    )


def test_ask_live_returns_text_and_latency() -> None:
    session = _session(_Reply())
    text, latency_ms = asyncio.run(ask_live(session, _probe(), Arm.FULL, None))
    assert text == "an answer"
    assert latency_ms >= 0


def test_ask_live_masks_the_named_scope_only_on_the_ablated_arm() -> None:
    session = _session(_Reply())
    asyncio.run(ask_live(session, _probe(), Arm.ABLATED, MemoryType.EPISODIC))
    assert session.last_gateway is not None
    assert session.last_gateway._masked_scope is MemoryType.EPISODIC


def test_ask_live_masks_nothing_on_the_control_arm() -> None:
    # control differs by having no seed, never by disabling a read.
    session = _session(_Reply())
    asyncio.run(ask_live(session, _probe(), Arm.CONTROL, None))
    assert session.last_gateway is not None
    assert session.last_gateway._masked_scope is None


def test_the_control_arm_is_never_seeded() -> None:
    # control differs by having no seed, never by disabling a read (SPEC §5.1).
    session = _session(_Reply())
    asyncio.run(ask_live(session, _probe(), Arm.CONTROL, None))
    assert session.seeded == set()


def test_a_non_control_arm_seeds_exactly_once_per_session() -> None:
    session = _session(_Reply())
    probe = _probe()
    asyncio.run(ask_live(session, probe, Arm.FULL, None))
    asyncio.run(ask_live(session, probe, Arm.FULL, None))
    assert len(session.seeded) == 1


def test_a_non_short_term_probe_is_asked_in_a_session_that_was_never_seeded() -> None:
    # Seeding in the probing session would leave the answer in the recent-turn
    # window and the scope under test would never be read. SPEC §7 step 5.
    reply = _Reply()
    session = LiveSession(
        identity=build_identity(_probe_set(), "m"),
        adapters=AdapterSet(),
        reply=reply,
        seed=SeedSpec(("a seeded line",), {}, (), None),
    )
    asyncio.run(ask_live(session, _probe(targets=MemoryType.EPISODIC), Arm.FULL, None))
    assert session.last_gateway is not None
    probing_turns = session.last_gateway._read_active_turns()
    assert not any("a seeded line" in (turn.user_message or "") for turn in probing_turns)


def test_a_short_term_probe_is_asked_in_the_seeded_session() -> None:
    session = LiveSession(
        identity=build_identity(_probe_set(), "m"),
        adapters=AdapterSet(),
        reply=_Reply(),
        seed=SeedSpec(("a seeded line",), {}, (), None),
    )
    asyncio.run(ask_live(session, _probe(targets=MemoryType.SHORT_TERM), Arm.FULL, None))
    assert session.last_gateway is not None
    turns = session.last_gateway._read_active_turns()
    assert any("a seeded line" in (turn.user_message or "") for turn in turns)


def test_teardown_deletes_every_gateway_it_is_given() -> None:
    gateways = [_Gateway(), _Gateway()]
    assert asyncio.run(teardown(gateways)) == 2  # type: ignore[arg-type]
    assert all(gateway.deleted == 1 for gateway in gateways)


def test_teardown_keeps_going_when_one_store_is_already_gone() -> None:
    # A failed teardown must never mask the run's actual results.
    gateways = [_Gateway(fail=True), _Gateway()]
    assert asyncio.run(teardown(gateways)) == 1  # type: ignore[arg-type]
    assert gateways[1].deleted == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py -q`
Expected: FAIL — `ImportError: cannot import name 'LiveSession'`

- [ ] **Step 3: Write the implementation**

Append to `src/cowork_agent/features/ai_chat/memory_eval/live_runner.py`:

```python
@dataclass
class LiveSession:
    """Everything one live run needs to ask a question under any arm.

    Mutable and not slotted, unlike the rest of this package: `last_gateway`
    records the gateway built for the most recent ask so teardown and tests can
    reach it without threading a return value through `AskProbe`, whose shape is
    fixed by the offline runner.
    """

    identity: RunIdentity
    adapters: AdapterSet
    reply: object
    seed: SeedSpec = field(default_factory=lambda: SeedSpec((), {}, (), None))
    company_rag_enabled: bool = True
    last_gateway: ArmScopedMemoryGateway | None = None
    gateways: list[ArmScopedMemoryGateway] = field(default_factory=list)
    seeded: set[str] = field(default_factory=set)
    seed_failures: list[str] = field(default_factory=list)


async def _seed_for(
    session: LiveSession, probe: Probe, arm: Arm, scope: ChatMemoryScope
) -> None:
    """Seed one arm's store, in a session that is never the probing session.

    Two rituals speak to the controller — short_term and episodic — and both
    therefore leave turns in whatever session they run in. Running them in the
    probing session would put the answer directly in the prompt's recent-turn
    window, and the probe would pass without the scope under test ever being
    read. SPEC §7 step 5.

    The single exception is a short_term probe, where the buffer IS the subject.
    That one seeds into the probing session on purpose.
    """

    seed_session_id = (
        scope.session_id if not needs_fresh_session(probe) else f"{scope.session_id}-seed"
    )
    seed_scope = ChatMemoryScope(
        tenant_id=scope.tenant_id, user_id=scope.user_id, session_id=seed_session_id
    )
    controller, gateway = build_arm_controller(
        seed_scope,
        session.adapters,
        session.reply,
        masked_scope=None,
        company_rag_enabled=session.company_rag_enabled,
    )
    session.gateways.append(gateway)

    outcomes = [
        await seed_long_term(
            gateway, seed_scope, session.seed, now=datetime.now(UTC), profile_id=session.identity.run_key
        ),
        await seed_episodic(controller, seed_session_id, session.seed, key_prefix=seed_session_id),
    ]
    if not needs_fresh_session(probe):
        outcomes.append(
            await seed_short_term(
                controller, seed_session_id, session.seed, key_prefix=seed_session_id
            )
        )
    session.seed_failures.extend(
        f"{outcome.scope.value}: {outcome.reason}" for outcome in outcomes if not outcome.ok
    )
    session.seed_failures.extend(
        finding.reason
        for finding in await verify_seed(
            gateway, seed_scope, tuple(outcome.scope for outcome in outcomes if outcome.ok)
        )
    )


async def ask_live(
    session: LiveSession,
    probe: Probe,
    arm: Arm,
    masked: MemoryType | None,
) -> tuple[str, int]:
    """The live `AskProbe`: seed the arm if needed, build its controller, ask.

    `masked` is supplied by the offline runner and is already `None` for
    everything except the ablated arm. It is passed through rather than
    recomputed so the two runners can never disagree about what an arm means.

    The control arm is never seeded. That is the whole of what makes it a
    control: it differs by having no seed, never by disabling a read (SPEC
    §5.1). Seeding it would turn it into a fourth ablation arm and destroy the
    leak signal.
    """

    scope = ChatMemoryScope(
        tenant_id=session.identity.tenant_id,
        user_id=session.identity.user_id,
        session_id=session_id_for(session.identity, probe, arm),
    )
    if arm is not Arm.CONTROL and scope.session_id not in session.seeded:
        session.seeded.add(scope.session_id)
        await _seed_for(session, probe, arm, scope)

    controller, gateway = build_arm_controller(
        scope,
        session.adapters,
        session.reply,
        masked_scope=masked,
        company_rag_enabled=session.company_rag_enabled,
    )
    session.last_gateway = gateway
    session.gateways.append(gateway)
    return await ask_once(
        controller, scope.session_id, probe.question, f"{probe.probe_id}-{arm.value}"
    )


async def teardown(gateways: Sequence[ArmScopedMemoryGateway]) -> int:
    """Delete every store this run created. Returns how many succeeded.

    The gateway method is `delete_all_memory`, not the `delete_all_for_user`
    named in SPEC §7 step 12 — that is the episodic port's method, called
    internally. Company RAG is never touched, by design.

    One failure never stops the rest: a run that produced real findings must
    still report them even if cleanup is partial.
    """

    deleted = 0
    for gateway in gateways:
        try:
            await gateway.delete_all_memory()
        except Exception:  # noqa: BLE001 - cleanup must not mask results
            continue
        deleted += 1
    return deleted
```

Final import block for the module:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import ChatMemoryScope, MemoryType

from .arms import Arm, ArmScopedMemoryGateway
from .live_controller import AdapterSet, ask_once, build_arm_controller
from .live_seeding import seed_episodic, seed_short_term, verify_seed
from .probes import Probe, ProbeSet, SeedSpec
from .runner import run_key
from .seeding import seed_long_term
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_live_runner.py -q`
Expected: PASS — 18 passed

- [ ] **Step 5: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval tests/unit/features/ai_chat/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/cowork_agent/features/ai_chat/memory_eval/live_runner.py tests/unit/features/ai_chat/memory_eval/test_live_runner.py
git commit -m "feat(memory-eval): live orchestration, arm sessions and teardown"
```

---

## Task 9: Retarget the isolation probe

Implements the correction described in *Findings* item 2. The semantic store cannot partition by tenant, so the isolation probe moves to a scope where isolation is real.

**Files:**
- Modify: `evaluations/MEMORIES/probes/v1-four-scopes.json`
- Modify: `evaluations/MEMORIES/README.md`
- Test: `tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py` (append)

The existing four tests in that file must keep passing: all four scopes covered, all four test types exercised, at least two probes per scope.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py`:

```python
def test_isolation_probes_target_a_scope_that_actually_partitions() -> None:
    # The company RAG corpus has no tenant field: KnowledgeChunk carries none,
    # allowed_chunk_indices filters only document/year/month, and load_corpus
    # accepts tenant_id without using it. A semantic isolation probe would
    # therefore report a leak that describes the store's design, not a
    # regression. long_term and episodic isolation is enforced in SQL.
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    isolation = [probe for probe in probe_set.probes if probe.test is ProbeTest.ISOLATION]
    assert isolation, "the probe set must still exercise the isolation test type"
    assert all(
        probe.targets in {MemoryType.LONG_TERM, MemoryType.EPISODIC} for probe in isolation
    )


def test_every_foreign_seed_probe_is_an_isolation_probe() -> None:
    probe_set = load_probe_set(json.loads(_PATH.read_text(encoding="utf-8")))
    for probe in probe_set.probes:
        if probe.foreign_seed:
            assert probe.test is ProbeTest.ISOLATION
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py -q`
Expected: FAIL — the shipped `sem_isolation_01` targets `semantic`

- [ ] **Step 3: Replace the two affected probes**

In `evaluations/MEMORIES/probes/v1-four-scopes.json`, replace the `sem_isolation_01` object with these two objects (the semantic scope keeps two probes, and isolation moves to `long_term`):

```json
    {
      "id": "sem_restraint_01",
      "targets": "semantic",
      "test": "restraint",
      "question": "What does our company policy say about the sabbatical scheme?",
      "expect_refusal": true,
      "note": "No sabbatical document exists in the corpus. Contains the semantic cue 'company policy', so the read fires and finds nothing - the only correct behaviour is to decline."
    },
    {
      "id": "lt_isolation_01",
      "targets": "long_term",
      "test": "isolation",
      "question": "What language and timezone do you have on file for me?",
      "expect_refusal": true,
      "foreign_seed": true,
      "note": "The profile is written under a DIFFERENT tenant_id and user_id, then this question is asked as the primary user. PostgresChatProfileRepository keys on the namespace and MemoryGateway._require_scope raises NamespaceAccessDenied, so any answer here is a real cross-tenant leak. Targeting semantic instead would be dishonest: the company RAG corpus has no tenant field at all."
    },
```

One probe becomes two, so the set grows from eight probes to nine. Keep every other probe as it is. Final counts:

| scope | probes | ids |
|---|---|---|
| `short_term` | 2 | `st_recall_01`, `st_update_01` |
| `long_term` | 3 | `lt_recall_01`, `lt_restraint_01`, `lt_isolation_01` |
| `episodic` | 2 | `ep_recall_01`, `ep_restraint_01` |
| `semantic` | 2 | `sem_recall_01`, `sem_restraint_01` |

All four scopes have at least two probes, and all four test types are exercised: `recall`, `update`, `restraint`, `isolation`. `lt_restraint_01` stays — it asks about a job title that was never set, which is a different failure from `lt_isolation_01`'s cross-tenant read.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Record the limitation in the README**

Append to `evaluations/MEMORIES/README.md`:

```markdown
## Known limitation: semantic tenancy

The company RAG corpus has no tenant partition. `KnowledgeChunk` carries no
tenant field, `allowed_chunk_indices` filters only on document id, year and
month, and `load_corpus(corpus_dir, *, tenant_id)` accepts a `tenant_id` it
never reads. Company knowledge is corpus-wide by design — `delete_all_memory`
documents that it never touches company RAG.

The isolation probe therefore targets `long_term`, where isolation is real and
enforced in SQL. A semantic isolation probe would report a leak on every run
that describes the store's design rather than a regression, which is worse than
having no probe at all.
```

- [ ] **Step 6: Confirm the dry run still works end to end**

Run: `PYTHONPATH=src python scripts/evaluate_memory.py --dry-run --output evaluations/MEMORIES/runs/dry-run.json`
Expected: exit 0, `"probe_count": 9`, no `leaked_probes`.

`PYTHONPATH=src` is required here: this is not pytest, so without it `import cowork_agent` resolves to the main worktree, which has no `memory_eval` package.

- [ ] **Step 7: Commit**

```bash
git add evaluations/MEMORIES/probes/v1-four-scopes.json evaluations/MEMORIES/README.md tests/unit/features/ai_chat/memory_eval/test_shipped_probe_set.py
git commit -m "fix(memory-eval): retarget isolation to a scope that actually partitions"
```

---

## Task 10: CLI wiring and the live smoke test

Implements SPEC §7 steps 2–12 end to end. Replaces the `return 2` PLAN.md Task 8 left behind.

**Files:**
- Modify: `scripts/evaluate_memory.py`
- Create: `tests/integration/memory_eval/test_live_smoke.py`
- Test: `tests/unit/scripts/test_evaluate_memory.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–9; `run_probe_set`, `load_probe_set` (PLAN.md Tasks 1, 8).
- Produces: `async def run_live(probe_set, env, *, model, provider) -> dict[str, object]` in the script.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/scripts/test_evaluate_memory.py`:

```python
def test_a_live_run_without_a_gemini_key_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No model means no reply to score, so there is no run at all. This is the
    # one dependency whose absence is fatal rather than a per-scope finding.
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"):
        monkeypatch.delenv(name, raising=False)
    code = main(["--probe-set", str(_probe_set_file(tmp_path))])
    assert code == 1


def test_dry_run_still_works_after_the_live_path_lands(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    assert main(
        ["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)]
    ) == 0
```

Create `tests/integration/memory_eval/test_live_smoke.py`:

```python
"""One end-to-end proof that the live tier runs (SPEC §7).

Marked `live`: needs PostgreSQL, a Gemini key and a Jina key. Deselected by
default, so the standard suite stays green without any of them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cowork_agent.features.ai_chat.memory_eval.live_env import (
    probe_environment,
    unavailable_scopes,
)
from cowork_agent.features.ai_chat.memory_eval.probes import load_probe_set

pytestmark = pytest.mark.live

_PROBE_SET = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")


def test_the_environment_reports_every_scope_available() -> None:
    env = probe_environment(dict(os.environ))
    missing = unavailable_scopes(env)
    if missing:
        pytest.skip(f"live dependencies missing: {[item.reason for item in missing]}")
    assert env.postgres_url is not None


def test_the_shipped_probe_set_runs_live_and_produces_one_row_per_probe() -> None:
    env = probe_environment(dict(os.environ))
    if unavailable_scopes(env) or not env.gemini_ready:
        pytest.skip("live dependencies missing")

    from scripts.evaluate_memory import main

    output = Path("evaluations/MEMORIES/runs/live-smoke.json")
    code = main(["--probe-set", str(_PROBE_SET), "--output", str(output)])
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    probe_set = load_probe_set(json.loads(_PROBE_SET.read_text(encoding="utf-8")))
    assert len(report["verdicts"]) == len(probe_set.probes)
    assert report["provider"] == "gemini"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/scripts/test_evaluate_memory.py -q`
Expected: FAIL — the live path still returns 2, so the first new test gets 2 instead of 1

- [ ] **Step 3: Write the live path in the CLI**

In `scripts/evaluate_memory.py`, add these imports:

```python
from cowork_agent.config import GeminiSettings, JinaEmbeddingSettings
from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_env import (
    LiveEnvironment,
    probe_environment,
    run_with_selector_loop,
    unavailable_scopes,
)
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    LiveSession,
    ask_live,
    build_identity,
    teardown,
)
from cowork_agent.features.ai_chat.memory_eval.live_seeding import (
    seed_episodic,
    seed_semantic,
    seed_short_term,
)
from cowork_agent.features.ai_chat.memory_eval.probes import ProbeSet
from cowork_agent.features.ai_chat.memory_eval.seeding import seed_long_term
from cowork_agent.integrations.llm.chat_reply import GeminiChatReply
from cowork_agent.integrations.rag.embeddings import JinaEmbeddingAdapter
from cowork_agent.persistence.migrate import apply_migrations
from cowork_agent.persistence.repositories.postgres import (
    PostgresChatProfileRepository,
    PostgresTaskEpisodeRepository,
)
```

Then replace the `if not args.dry_run:` block with:

```python
    if args.dry_run:
        report = asyncio.run(_dry_run(probe_set))
    else:
        env = probe_environment(dict(os.environ))
        if not env.gemini_ready:
            print(
                "ERROR: no GEMINI_API_KEY. Without a model there is no reply to "
                "score, so there is no run.",
                file=sys.stderr,
            )
            return 1
        report = run_with_selector_loop(
            run_live(probe_set, env, provider="gemini", model=GeminiSettings.from_env().model)
        )
```

And add the orchestration function:

```python
async def _build_adapters(
    env: LiveEnvironment, probe_set: ProbeSet
) -> tuple[AdapterSet, list[str], object | None]:
    """Build every adapter the environment can support. Absences are findings."""

    from psycopg_pool import AsyncConnectionPool

    failures: list[str] = []
    declarative = episodic = None
    pool = None
    if env.postgres_url is not None:
        pool = AsyncConnectionPool(env.postgres_url, min_size=1, max_size=4, open=False)
        await pool.open(wait=True)
        await apply_migrations(pool)
        declarative = PostgresChatProfileRepository(pool)
        episodic = PostgresTaskEpisodeRepository(pool)
    else:
        failures.append("postgres unavailable: long_term and episodic are unscorable")

    semantic = None
    if env.jina_ready:
        outcome, adapter = await seed_semantic(
            probe_set.seed, JinaEmbeddingAdapter(JinaEmbeddingSettings.from_env()),
            corpus_root=Path("."),
        )
        if outcome.ok:
            semantic = adapter
        else:
            failures.append(f"semantic: {outcome.reason}")
    else:
        failures.append("no JINA_API_KEY: semantic is unscorable")

    return AdapterSet(declarative, episodic, semantic), failures, pool


async def run_live(
    probe_set: ProbeSet, env: LiveEnvironment, *, provider: str, model: str
) -> dict[str, object]:
    """Seed, probe under three arms, report, then delete everything created."""

    identity = build_identity(probe_set, model)
    adapters, failures, pool = await _build_adapters(env, probe_set)
    failures.extend(item.reason for item in unavailable_scopes(env))
    session = LiveSession(
        identity=identity,
        adapters=adapters,
        reply=GeminiChatReply.from_settings(GeminiSettings.from_env()),
        seed=probe_set.seed,
    )
    try:
        report = await run_probe_set(
            probe_set,
            lambda probe, arm, masked: ask_live(session, probe, arm, masked),
            provider=provider,
            model=model,
            ran_at=datetime.now(UTC),
            seed_failures=failures,
        )
        # Seeding happens inside ask_live, so session.seed_failures is only
        # complete once run_probe_set has returned. Passing it as an argument
        # above would capture an empty list on every run, because arguments are
        # evaluated before the call.
        report["seed_failures"] = sorted({*failures, *session.seed_failures})
    finally:
        await teardown(session.gateways)
        if pool is not None:
            await pool.close()
    return report
```

Seeding order needs no extra wiring here: `run_probe_set` iterates probes and arms, and Task 8's `ask_live` already seeds each non-control session on first use. `run_live` only has to pass `seed=probe_set.seed` into `LiveSession` and forward `session.seed_failures` into the report, which the code above does.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/scripts/test_evaluate_memory.py -q`
Expected: PASS — 9 passed

- [ ] **Step 5: Confirm the live tests are deselected by default**

Run: `python -m pytest tests/integration/memory_eval -q`
Expected: no tests run; the banner names the deselected `live` module

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — every test green, live deselected

- [ ] **Step 7: Lint and type-check**

Run: `python -m ruff check src/cowork_agent/features/ai_chat/memory_eval scripts/evaluate_memory.py tests/unit/features/ai_chat/memory_eval tests/unit/scripts/test_evaluate_memory.py tests/integration/memory_eval`
Run: `python -m mypy src/cowork_agent/features/ai_chat/memory_eval`
Expected: both clean

- [ ] **Step 8: Commit**

```bash
git add scripts/evaluate_memory.py tests/integration/memory_eval tests/unit/scripts/test_evaluate_memory.py src/cowork_agent/features/ai_chat/memory_eval
git commit -m "feat(memory-eval): live tier wired end to end behind the live marker"
```

---

## Spec coverage

| SPEC section | Task | Status after this plan |
|---|---|---|
| §6 `short_term` ritual | 3 | Covered |
| §6 `long_term` ritual | — | Already shipped (PLAN.md Task 7) |
| §6 `episodic` ritual | 4 | Covered, via `approve_task_episode` rather than a raw gateway transition |
| §6 `semantic` ritual | 5 | Covered with `InRepoSemanticMemory` + the fixture corpus |
| §6.1 seed failures are findings | 3, 4, 5, 10 | Covered — every ritual returns `SeedOutcome`; failures reach `seed_failures` |
| §6.2 isolation seeding | 9 | **Retargeted to `long_term`.** Semantic tenancy does not exist in the code; see Findings 2 |
| §7 step 1 identity | 7 | Covered |
| §7 step 2 arm loop | 8, 10 | Covered — the offline `run_probe_set` owns the loop |
| §7 step 3 seed, skip on control | 10 | Covered, with an explicit test |
| §7 step 4 verify the seed | 6 | Covered |
| §7 step 5 fresh session | 7 | Covered |
| §7 step 6 ask the probe | 2 | Covered |
| §7 steps 7–11 scoring, verdicts, report | — | Already shipped (PLAN.md Tasks 2, 3, 4, 6) |
| §7 step 12 teardown | 8 | Covered — `delete_all_memory()`, not the spec's `delete_all_for_user()` |

## Open work after Task 10

Tasks 1–10 are implemented, committed and green. Everything below was deliberately left out of this plan, and each item is recorded here so it is read as a known gap rather than rediscovered as a bug.

**1. Foreign-tenant seeding is declared but not executed.** Task 9 retargets `lt_isolation_01` to `long_term` and `RunIdentity` carries `foreign_tenant_id` / `foreign_user_id`, but nothing writes the foreign profile yet.

*Consequence, stated plainly:* `lt_isolation_01` reports **`broken`** on every live run until this lands. That is honest and visible — the probe asks for material that was never seeded, so it gets a refusal from an empty store rather than from real isolation. It proves nothing about tenancy either way, so it must not be read as a passing isolation check.

*What it needs:* a second `ArmScopedMemoryGateway` built at the foreign scope, one `seed_long_term` call against it, that gateway appended to `session.gateways` so teardown reaches it, and — the reason it is separate — its own test that the primary user genuinely **cannot** read the foreign profile. Without that test the wiring would be indistinguishable from a probe that passes because nothing was ever written.

**2. Semantic tenancy is a production change, not a harness change.** If company RAG ever needs per-tenant partitioning, `KnowledgeChunk` needs a tenant field, `allowed_chunk_indices` needs a filter, and `load_corpus`'s ignored `tenant_id` parameter needs to reach both. Until then, SPEC §6.2 forbids an isolation probe from targeting `semantic`, and `test_isolation_probes_target_a_scope_that_actually_partitions` enforces it.

**3. `write_chat_summary` has no production caller.** The port and the gateway method exist; no consolidation loop invokes them. So `ChatSummaryEpisode` is reachable in tests and unreachable in the product, and the episodic scope this harness measures is task episodes only. Found during the original investigation and deliberately out of scope — noted so a future reader does not assume summary episodes are covered.

**4. The launch gate is not in CI.** `evaluate_launch_gate` and its `DeterministicPairedScorer` are untouched by this work (SPEC §1.2). Bridging real outcomes into that gate needs a defensible outcome-to-score mapping first, which is its own decision with its own justification burden.

**5. The live tier has never been executed end to end.** It has no PostgreSQL server, no Gemini key and no Jina key on this machine, so `tests/integration/memory_eval/test_live_smoke.py` skips. Tasks 1–8 are unit-tested against fakes, which proves the wiring and not the behaviour. Expect the first real run to surface finding 3b (an episode is written only when the reply provider also returns a `ChatTaskProposal`) as the most likely episodic seed failure.
