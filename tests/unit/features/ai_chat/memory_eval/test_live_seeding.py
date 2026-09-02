from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

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

_CORPUS = "tests/fixtures/memory_eval/corpus"


class _Reply:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    async def stream_reply(self, request: object, context: object):
        del request, context
        self.calls += 1
        if self._fail:
            raise RuntimeError("model down")
        yield "acknowledged"


def _controller(reply: object):
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    return build_arm_controller(scope, AdapterSet(), reply, masked_scope=None)


def test_seed_short_term_lifecycle_and_failures() -> None:
    reply = _Reply()
    controller, gateway = _controller(reply)
    spec = SeedSpec(("line one", "line two", "line three"), {}, (), None)
    outcome = asyncio.run(seed_short_term(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is True
    assert outcome.scope is MemoryType.SHORT_TERM
    assert reply.calls == 3
    turns = gateway.read_active_turns()
    assert any("line one" in (turn.user_message or "") for turn in turns)

    # Empty spec is skip
    assert (
        asyncio.run(
            seed_short_term(controller, "s", SeedSpec((), {}, (), None), key_prefix="seed")
        ).reason
        == "nothing declared"
    )

    # Model failure is reported
    fail_ctrl, _ = _controller(_Reply(fail=True))
    fail_outcome = asyncio.run(
        seed_short_term(fail_ctrl, "s", SeedSpec(("line one",), {}, (), None), key_prefix="seed")
    )
    assert fail_outcome.ok is False and "model down" in fail_outcome.reason


class _EpisodicStore:
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
    def __init__(self, proposal: ChatTaskProposal | None) -> None:
        self._proposal = proposal

    async def stream_reply(self, request: object, context: object):
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


def test_seed_episodic_lifecycle_and_transition() -> None:
    store = _EpisodicStore()
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    controller, _ = build_arm_controller(
        scope, AdapterSet(episodic_memory=store), _ProposingReply(_proposal()), masked_scope=None
    )

    # Approved seed transitions to USER_APPROVED
    spec = SeedSpec(
        (), {}, (EpisodeSeed(request="Create a task to renew the CCCD", approve=True),), None
    )
    outcome = asyncio.run(seed_episodic(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is True
    assert ValidationStatus.USER_APPROVED in [status for _, status in store.transitions]

    # Unapproved seed written without transition
    store2 = _EpisodicStore()
    ctrl2, _ = build_arm_controller(
        scope, AdapterSet(episodic_memory=store2), _ProposingReply(_proposal()), masked_scope=None
    )
    spec_unapproved = SeedSpec(
        (), {}, (EpisodeSeed(request="Create a task to renew the CCCD", approve=False),), None
    )
    assert asyncio.run(seed_episodic(ctrl2, "s", spec_unapproved, key_prefix="seed")).ok is True
    assert store2.transitions == []

    # Non-task request or no proposal
    bad_spec = SeedSpec((), {}, (EpisodeSeed(request="what is the weather", approve=True),), None)
    assert asyncio.run(seed_episodic(controller, "s", bad_spec, key_prefix="seed")).ok is False


class _Embedder:
    def __init__(self, fail: bool = False, model: str = "fake") -> None:
        self._fail = fail
        self.model = model
        self.tasks: list[str] = []

    async def embed(self, texts: tuple[str, ...], *, task: str = "") -> list[list[float]]:
        self.tasks.append(task)
        if self._fail:
            raise RuntimeError("embedder down")
        vocabulary = ("overtime", "manager", "approval", "leave", "portal", "annual")
        return [[float(text.casefold().count(word)) + 1.0 for word in vocabulary] for text in texts]


def test_seed_semantic_lifecycle_and_caching(tmp_path: Path) -> None:
    spec = SeedSpec((), {}, (), _CORPUS)
    first = _Embedder()
    outcome, adapter = asyncio.run(
        seed_semantic(spec, first, corpus_root=Path("."), cache_dir=tmp_path)
    )
    assert outcome.ok is True
    assert adapter is not None
    assert "retrieval.passage" in first.tasks

    # Cache hit skips passage embeds
    second = _Embedder()
    outcome2, adapter2 = asyncio.run(
        seed_semantic(spec, second, corpus_root=Path("."), cache_dir=tmp_path)
    )
    assert outcome2.ok is True
    assert "retrieval.passage" not in second.tasks

    # Corrupt cache rebuilds
    for npz in tmp_path.glob("*.npz"):
        npz.write_bytes(b"not-an-npz")
    rebuilt = _Embedder()
    outcome3, adapter3 = asyncio.run(
        seed_semantic(spec, rebuilt, corpus_root=Path("."), cache_dir=tmp_path)
    )
    assert outcome3.ok is True and "retrieval.passage" in rebuilt.tasks


def test_verify_seed_detection() -> None:
    class _SearchBlindStore(_EpisodicStore):
        async def read_episodes(self, namespace: object, query: object) -> tuple[TaskEpisode, ...]:
            return ()

    now = datetime(2026, 8, 19, tzinfo=UTC)
    ep = TaskEpisode(
        episode_id="ep-1",
        record_id="rec-1",
        user_id="u",
        chat_session_id="s",
        chat_turn_id="turn-1",
        creation_reason="explicit_user_task_request",
        task_title="Gia hạn CCCD",
        minimal_request_paraphrase="Tạo tác vụ",
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
    store = _SearchBlindStore()
    store.episodes["ep-1"] = ep

    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    _, gateway = build_arm_controller(
        scope, AdapterSet(episodic_memory=store), _ProposingReply(_proposal()), masked_scope=None
    )
    findings = asyncio.run(verify_seed(gateway, scope, (MemoryType.EPISODIC,)))
    assert len(findings) == 1
    assert "1 episode" in findings[0].reason
