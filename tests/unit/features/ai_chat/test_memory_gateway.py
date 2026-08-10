import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatToolChoice,
    ChatTurn,
    DeclarativeProfile,
    DegradedMemorySource,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryRead,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.memory_gateway import (
    MemoryGateway,
    MemorySourceUnavailableError,
    NamespaceAccessDenied,
)
from cowork_agent.features.ai_chat.profile_policy import ProfileWriteRejected
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


def _scope(
    *,
    tenant_id: str = "tenant-1",
    user_id: str = "user@example.com",
    session_id: str = "session-1",
) -> ChatMemoryScope:
    return ChatMemoryScope(tenant_id=tenant_id, user_id=user_id, session_id=session_id)


def _request(scope: ChatMemoryScope, *, all_sources: bool = True) -> MemoryContextRequest:
    return MemoryContextRequest(
        session_id=scope.session_id,
        scope=scope,
        reads=MemoryReadOptions(
            short_term=True,
            long_term=all_sources,
            episodic=EpisodicMemoryRead(
                enabled=all_sources,
                retrieval_eligible_only=True,
                max_items=2,
            ),
            semantic=SemanticMemoryRead(enabled=all_sources),
        ),
    )


def _turn() -> ChatTurn:
    return ChatTurn(
        turn_id="turn-1",
        session_id="session-1",
        user_message="What should I do?",
        assistant_message="Start with the approved procedure.",
        created_at=NOW,
    )


def _profile() -> DeclarativeProfile:
    return DeclarativeProfile(
        profile_id="profile-1",
        tenant_id="tenant-1",
        user_id="user@example.com",
        language="en",
        timezone="Asia/Bangkok",
        assistant_persona="Coworker",
        response_tone="direct",
        created_at=NOW,
        updated_at=NOW,
    )


def _episode(
    *, episode_id: str, status: ValidationStatus, session_id: str = "session-1"
) -> TaskEpisode:
    return TaskEpisode(
        episode_id=episode_id,
        record_id=episode_id,
        tenant_id="tenant-1",
        user_id="user@example.com",
        run_id="run-1",
        chat_session_id=session_id,
        chat_turn_id="turn-1",
        source_tool="@Email",
        gmail_message_id="message-1",
        gmail_url="https://mail.google.com/mail/u/0/#all/message-1",
        task_title="Submit the report",
        minimal_request_paraphrase="Submit the requested report.",
        action_plan=("Open the approved template.",),
        rag_citations=(
            EpisodeCitation(
                document_id="doc-1",
                document_title="Procedure",
                section=None,
                source_url="https://docs.example.com/procedure",
            ),
        ),
        missing_information=(),
        validation_status=status,
        retrieval_eligible=status in {ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED},
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TOOL_OUTPUT,
        created_at=NOW,
        updated_at=NOW,
        pipeline_version="2",
        model_id=None,
        prompt_version=None,
        confidence=None,
    )


class ProfileReader:
    def __init__(self, profile: DeclarativeProfile | None = None) -> None:
        self.profile = profile
        self.calls: list[MemoryNamespace] = []
        self.writes: list[DeclarativeProfile] = []
        self.deletes: list[MemoryNamespace] = []

    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        self.calls.append(namespace)
        return self.profile

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        del namespace
        self.writes.append(profile)
        self.profile = profile
        return profile

    async def delete_profile(self, namespace: MemoryNamespace) -> bool:
        self.deletes.append(namespace)
        existed = self.profile is not None
        self.profile = None
        return existed


class EpisodeReader:
    def __init__(self, episodes: tuple[TaskEpisode, ...]) -> None:
        self.episodes = episodes
        self.calls: list[tuple[MemoryNamespace, int]] = []

    async def read_episodes(
        self, namespace: MemoryNamespace, *, max_items: int
    ) -> tuple[TaskEpisode, ...]:
        self.calls.append((namespace, max_items))
        return self.episodes


class SemanticReader:
    def __init__(self, context: Mapping[str, object] | None = None) -> None:
        self.context = context
        self.calls: list[MemoryNamespace] = []

    async def read_semantic_context(
        self, namespace: MemoryNamespace
    ) -> Mapping[str, object] | None:
        self.calls.append(namespace)
        return self.context


class UnavailableProfileReader:
    async def read_profile(self, namespace: MemoryNamespace) -> DeclarativeProfile | None:
        del namespace
        raise MemorySourceUnavailableError("sensitive provider detail")

    async def write_profile(
        self, namespace: MemoryNamespace, profile: DeclarativeProfile
    ) -> DeclarativeProfile:
        del namespace, profile
        raise MemorySourceUnavailableError("sensitive provider detail")

    async def delete_profile(self, namespace: MemoryNamespace) -> bool:
        del namespace
        raise MemorySourceUnavailableError("sensitive provider detail")


def _gateway(
    *,
    profile_reader: ProfileReader | UnavailableProfileReader | None = None,
    episode_reader: EpisodeReader | None = None,
    semantic_reader: SemanticReader | None = None,
) -> MemoryGateway:
    return MemoryGateway(
        scope=_scope(),
        session_buffer=InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60),
        declarative_memory=profile_reader,
        episodic_memory=episode_reader,
        semantic_memory=semantic_reader,
    )


def test_gateway_assembles_typed_context_and_enforces_eligibility_and_bound() -> None:
    approved = _episode(episode_id="approved", status=ValidationStatus.USER_APPROVED)
    completed = _episode(episode_id="completed", status=ValidationStatus.COMPLETED)
    unvalidated = _episode(
        episode_id="unvalidated", status=ValidationStatus.SYSTEM_GENERATED
    )
    extra = _episode(episode_id="extra", status=ValidationStatus.USER_APPROVED)
    profiles = ProfileReader(_profile())
    episodes = EpisodeReader((unvalidated, approved, completed, extra))
    semantic = SemanticReader({"citation_ids": ["doc-1#0"]})
    gateway = _gateway(
        profile_reader=profiles,
        episode_reader=episodes,
        semantic_reader=semantic,
    )
    gateway.append_turn(_turn())

    response = asyncio.run(gateway.read_context(_request(_scope())))

    assert response.turns == (_turn(),)
    assert response.profile == _profile()
    assert response.episodes == (approved, completed)
    assert response.to_dict()["semantic_context"] == {"citation_ids": ["doc-1#0"]}
    assert response.degraded is False
    assert response.degraded_sources == ()
    assert episodes.calls[0][1] == 2


@pytest.mark.parametrize(
    "foreign_scope",
    [
        _scope(tenant_id="tenant-2"),
        _scope(user_id="other@example.com"),
        _scope(session_id="session-2"),
    ],
    ids=["tenant", "user", "session"],
)
def test_gateway_rejects_foreign_scope_before_calling_adapters(
    foreign_scope: ChatMemoryScope,
) -> None:
    profiles = ProfileReader(_profile())
    episodes = EpisodeReader(())
    semantic = SemanticReader({})
    gateway = _gateway(
        profile_reader=profiles,
        episode_reader=episodes,
        semantic_reader=semantic,
    )

    with pytest.raises(NamespaceAccessDenied):
        asyncio.run(gateway.read_context(_request(foreign_scope)))

    assert profiles.calls == []
    assert episodes.calls == []
    assert semantic.calls == []


def test_gateway_reports_each_requested_unavailable_optional_source() -> None:
    response = asyncio.run(_gateway().read_context(_request(_scope())))

    assert response.turns == ()
    assert response.profile is None
    assert response.episodes == ()
    assert response.semantic_context is None
    assert response.degraded is True
    assert response.degraded_sources == (
        DegradedMemorySource.LONG_TERM,
        DegradedMemorySource.EPISODIC,
        DegradedMemorySource.SEMANTIC,
    )


def test_gateway_allows_eligible_prior_session_episodes_for_the_same_user() -> None:
    prior = _episode(
        episode_id="prior-session",
        status=ValidationStatus.USER_APPROVED,
        session_id="session-older",
    )
    gateway = _gateway(
        profile_reader=ProfileReader(None),
        episode_reader=EpisodeReader((prior,)),
        semantic_reader=SemanticReader(None),
    )

    response = asyncio.run(gateway.read_context(_request(_scope())))

    assert response.episodes == (prior,)


def test_typed_optional_source_failure_degrades_without_leaking_error_text() -> None:
    response = asyncio.run(
        _gateway(
            profile_reader=UnavailableProfileReader(),
            episode_reader=EpisodeReader(()),
            semantic_reader=SemanticReader(None),
        ).read_context(_request(_scope()))
    )

    assert response.degraded_sources == (DegradedMemorySource.LONG_TERM,)
    assert "sensitive provider detail" not in str(response.to_dict())


def _explicit_provenance(source_tool: ChatToolChoice | None = None) -> MemoryProvenance:
    return MemoryProvenance(
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
        source_id="chat-settings-form",
        source_tool=source_tool,
        run_id=None,
        chat_turn_id="turn-1",
        pipeline_version=None,
        model_id=None,
        prompt_version=None,
    )


def test_explicit_profile_write_persists_and_later_reads_return_it() -> None:
    profiles = ProfileReader(None)
    gateway = _gateway(profile_reader=profiles)

    written = asyncio.run(gateway.write_profile(_profile(), provenance=_explicit_provenance()))

    assert written == _profile()
    assert profiles.writes == [_profile()]
    assert asyncio.run(gateway.read_context(_request(_scope()))).profile == _profile()


def test_gateway_refuses_a_non_explicit_profile_write_before_the_adapter() -> None:
    profiles = ProfileReader(None)
    gateway = _gateway(profile_reader=profiles)

    with pytest.raises(ProfileWriteRejected):
        asyncio.run(
            gateway.write_profile(
                _profile(), provenance=_explicit_provenance(ChatToolChoice.EMAIL)
            )
        )

    assert profiles.writes == []


def test_gateway_refuses_a_foreign_scope_profile_write() -> None:
    profiles = ProfileReader(None)
    foreign = DeclarativeProfile(
        profile_id="profile-2",
        tenant_id="tenant-2",
        user_id="user@example.com",
        language="en",
        timezone=None,
        assistant_persona=None,
        response_tone=None,
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(NamespaceAccessDenied):
        asyncio.run(
            _gateway(profile_reader=profiles).write_profile(
                foreign, provenance=_explicit_provenance()
            )
        )

    assert profiles.writes == []


def test_profile_deletion_prevents_later_retrieval() -> None:
    profiles = ProfileReader(_profile())
    gateway = _gateway(profile_reader=profiles)

    assert asyncio.run(gateway.delete_profile()) is True
    assert asyncio.run(gateway.read_context(_request(_scope()))).profile is None
    assert profiles.deletes[0].memory_type is MemoryType.LONG_TERM


def test_profile_write_failure_surfaces_instead_of_degrading_silently() -> None:
    gateway = _gateway(profile_reader=UnavailableProfileReader())

    with pytest.raises(MemorySourceUnavailableError):
        asyncio.run(gateway.write_profile(_profile(), provenance=_explicit_provenance()))


def test_profile_outage_leaves_working_memory_and_tool_availability_intact() -> None:
    gateway = _gateway(
        profile_reader=UnavailableProfileReader(),
        episode_reader=EpisodeReader(()),
        semantic_reader=SemanticReader(None),
    )
    gateway.append_turn(_turn())

    response = asyncio.run(gateway.read_context(_request(_scope())))

    assert response.turns == (_turn(),)
    assert response.profile is None
    assert response.degraded_sources == (DegradedMemorySource.LONG_TERM,)


def test_disabled_optional_sources_are_not_called_or_reported_degraded() -> None:
    profiles = ProfileReader(_profile())
    episodes = EpisodeReader(())
    semantic = SemanticReader({})
    gateway = _gateway(
        profile_reader=profiles,
        episode_reader=episodes,
        semantic_reader=semantic,
    )

    response = asyncio.run(gateway.read_context(_request(_scope(), all_sources=False)))

    assert response.degraded is False
    assert profiles.calls == []
    assert episodes.calls == []
    assert semantic.calls == []


def test_gateway_rejects_turn_from_another_session_before_buffer_write() -> None:
    gateway = _gateway()
    foreign_turn = ChatTurn(
        turn_id="turn-1",
        session_id="session-2",
        user_message="Wrong session",
        assistant_message=None,
        created_at=NOW,
    )

    with pytest.raises(NamespaceAccessDenied):
        gateway.append_turn(foreign_turn)

    response = asyncio.run(gateway.read_context(_request(_scope(), all_sources=False)))
    assert response.turns == ()


def test_gateway_clear_session_is_idempotent() -> None:
    gateway = _gateway()
    gateway.append_turn(_turn())

    gateway.clear_session()
    gateway.clear_session()

    response = asyncio.run(gateway.read_context(_request(_scope(), all_sources=False)))
    assert response.turns == ()


def test_gateway_exposes_no_durable_or_semantic_write_operation() -> None:
    public_methods = {
        name
        for name, value in vars(MemoryGateway).items()
        if callable(value) and not name.startswith("_")
    }

    # V2-M2 adds explicit declarative writes only; episodic and semantic
    # writes must stay impossible through the gateway.
    assert public_methods == {
        "append_turn",
        "read_context",
        "clear_session",
        "write_profile",
        "delete_profile",
    }
