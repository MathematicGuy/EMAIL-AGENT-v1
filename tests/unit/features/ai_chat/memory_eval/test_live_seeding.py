from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    EpisodeSourceType,
    MemoryType,
    TaskEpisode,
    ValidationStatus,
)
from cowork_agent.features.ai_chat.memory_eval.live_controller import (
    AdapterSet,
    build_arm_controller,
)
from cowork_agent.features.ai_chat.memory_eval.live_seeding import (
    seed_episodic,
    seed_semantic,
    seed_short_term,
    verify_seed,
)
from cowork_agent.features.ai_chat.memory_eval.probes import EpisodeSeed, SeedSpec
from cowork_agent.features.ai_chat.ports import ChatReplyChunk, ChatTaskProposal

pytestmark = pytest.mark.extended

_CORPUS = "tests/fixtures/memory_eval/corpus"


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
    outcome = asyncio.run(
        seed_short_term(controller, "s", SeedSpec((), {}, (), None), key_prefix="seed")
    )
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

    async def read_episodes(self, namespace: object, query: object) -> tuple[TaskEpisode, ...]:
        del namespace, query
        return tuple(self.episodes.values())

    async def list_episodes(
        self, namespace: object, *, limit: int = 100
    ) -> tuple[TaskEpisode, ...]:
        del namespace, limit
        return tuple(self.episodes.values())

    async def transition_task_episode(self, transition: object) -> TaskEpisode | None:
        episode_id = getattr(transition, "episode_id", "")
        to_status = getattr(transition, "to_status", None)
        self.transitions.append((episode_id, to_status))
        return self.episodes.get(episode_id)


class _ProposingReply:
    """A reply port that returns a task proposal, as a task-capable provider does.

    `is_explicit_task_request` is necessary but not sufficient: the controller
    only writes an episode when the PROVIDER also returns a ChatTaskProposal.
    A plain-string reply produces a `task_episode_unavailable` error event and
    no episode at all.
    """

    def __init__(self, proposal: ChatTaskProposal | None) -> None:
        self._proposal = proposal

    async def stream_reply(self, request: object, context: object):  # noqa: ANN201 - structural
        del context
        yield ChatReplyChunk(
            text=f"Noted: {getattr(request, 'user_message', '')}",
            task_proposal=self._proposal,
        )


def _proposal() -> ChatTaskProposal:
    return ChatTaskProposal(
        task_title="Renew the CCCD",
        minimal_request_paraphrase="Renew the CCCD for the Da Nang office",
        action_plan=("Collect documents", "Submit the renewal"),
        rag_citations=(),
        missing_information=(),
        model_id="fake-model",
        prompt_version="v1",
        confidence=0.9,
    )


def _episodic_controller(store: _EpisodicStore, *, proposal: ChatTaskProposal | None = None):  # noqa: ANN201
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    return build_arm_controller(
        scope,
        AdapterSet(episodic_memory=store),
        _ProposingReply(proposal if proposal is not None else _proposal()),
        masked_scope=None,
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


def test_a_turn_that_creates_no_episode_is_a_finding_stating_only_what_happened() -> None:
    # is_explicit_task_request refuses this phrasing, so no episode is created.
    # That is a finding, not a crash — but the finding may not NAME that cause.
    # A turn creates no episode when the provider errors too, and the reason
    # used to assert the phrasing was rejected without ever checking.
    store = _EpisodicStore()
    controller, _ = _episodic_controller(store)
    spec = SeedSpec((), {}, (EpisodeSeed(request="what is the weather", approve=True),), None)
    outcome = asyncio.run(seed_episodic(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is False
    assert "no task episode" in outcome.reason
    assert "is_explicit_task_request" not in outcome.reason


def test_nothing_declared_is_a_skip() -> None:
    store = _EpisodicStore()
    controller, _ = _episodic_controller(store)
    outcome = asyncio.run(
        seed_episodic(controller, "s", SeedSpec((), {}, (), None), key_prefix="seed")
    )
    assert outcome.ok is True
    assert outcome.reason == "nothing declared"


def test_a_provider_that_returns_no_proposal_is_a_finding() -> None:
    # Distinct from bad phrasing: is_explicit_task_request accepts this request,
    # but the controller still writes no episode because the provider returned
    # no ChatTaskProposal. On a live run this is a model-behaviour finding, and
    # it must not be reported as episodic amnesia.
    store = _EpisodicStore()
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    controller, _ = build_arm_controller(
        scope, AdapterSet(episodic_memory=store), _ProposingReply(None), masked_scope=None
    )
    spec = SeedSpec(
        (), {}, (EpisodeSeed(request="Create a task to renew the CCCD", approve=True),), None
    )
    outcome = asyncio.run(seed_episodic(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is False
    assert store.episodes == {}


class _Embedder:
    """Deterministic bag-of-words embedder. No network, stable across runs."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def embed(self, texts: tuple[str, ...], *, task: str = "") -> list[list[float]]:
        del task
        if self._fail:
            raise RuntimeError("embedder down")
        vocabulary = ("overtime", "manager", "approval", "leave", "portal", "annual")
        return [[float(text.casefold().count(word)) + 1.0 for word in vocabulary] for text in texts]


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
    outcome, adapter = asyncio.run(seed_semantic(spec, _Embedder(fail=True), corpus_root=Path(".")))
    assert outcome.ok is False
    assert "embedder down" in outcome.reason
    assert adapter is None


# --- verification: "was it written" is a different question from "can we find it"


class _SearchBlindEpisodicStore(_EpisodicStore):
    """Holds rows that the retrieval query cannot match.

    This is the shape Postgres actually has. `search_vector @@
    plainto_tsquery('simple', ...)` ANDs every token of the query text, so a
    whole natural-language question matches nothing even when the episode is
    sitting in the table. A verification that only searched reported that as
    an empty store on every arm.
    """

    async def read_episodes(self, namespace: object, query: object) -> tuple[TaskEpisode, ...]:
        del namespace, query
        return ()


def _stored_episode() -> TaskEpisode:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return TaskEpisode(
        episode_id="ep-1",
        record_id="rec-1",
        user_id="u",
        chat_session_id="s",
        chat_turn_id="turn-1",
        creation_reason="explicit_user_task_request",
        task_title="Gia hạn CCCD cho văn phòng Đà Nẵng",
        minimal_request_paraphrase="Tạo tác vụ gia hạn CCCD",
        action_plan=("Thu thập hồ sơ",),
        rag_citations=(),
        missing_information=(),
        validation_status=ValidationStatus.USER_APPROVED,
        retrieval_eligible=True,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=now,
        updated_at=now,
        pipeline_version="v1",
        model_id="fake-model",
        prompt_version="v1",
        confidence=0.9,
    )


def _verify_episodic(store: _EpisodicStore) -> tuple[object, ...]:
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    _, gateway = build_arm_controller(
        scope, AdapterSet(episodic_memory=store), _ProposingReply(_proposal()), masked_scope=None
    )
    return asyncio.run(verify_seed(gateway, scope, (MemoryType.EPISODIC,)))


def test_a_stored_episode_the_query_cannot_find_is_reported_as_a_retrieval_failure() -> None:
    # The row is there. Reporting "the store is empty" here sent every reader
    # to the write path, which was never broken.
    store = _SearchBlindEpisodicStore()
    store.episodes["ep-1"] = _stored_episode()
    findings = _verify_episodic(store)
    assert len(findings) == 1
    reason = findings[0].reason
    assert "1 episode" in reason
    assert "retriev" in reason
    assert "came back empty" not in reason


def test_an_episodic_scope_with_no_stored_rows_is_reported_as_an_empty_store() -> None:
    findings = _verify_episodic(_SearchBlindEpisodicStore())
    assert len(findings) == 1
    assert "nothing was written" in findings[0].reason


def test_an_episode_the_query_finds_produces_no_finding() -> None:
    store = _EpisodicStore()
    store.episodes["ep-1"] = _stored_episode()
    assert _verify_episodic(store) == ()
