import asyncio
import inspect
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatSummaryEpisode,
    ChatTurn,
    DeclarativeProfile,
    DegradedMemorySource,
    EpisodeCitation,
    EpisodeSourceType,
    EpisodeTransition,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryQuery,
    SemanticMemoryRead,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.episode_policy import (
    ChatSummaryWriteRejected,
    TaskEpisodeTransitionRejected,
    TaskEpisodeWriteRejected,
)
from cowork_agent.features.ai_chat.memory_gateway import (
    MemoryGateway,
    MemorySourceUnavailableError,
    NamespaceAccessDenied,
)
from cowork_agent.features.ai_chat.memory_observability import (
    MemoryOperation,
    MemoryOutcome,
    RecordingMemoryOperationSink,
)
from cowork_agent.features.ai_chat.profile_policy import ProfileWriteRejected
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.persistence.repositories.postgres import PostgresChatProfileRepository

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
            episodic=(
                EpisodicMemoryQuery(
                    query="Find related approved task history.",
                    max_items=2,
                    min_score=0.5,
                    timeout_ms=500,
                )
                if all_sources
                else EpisodicMemoryRead(
                    enabled=False, retrieval_eligible_only=True, max_items=2
                )
            ),
            semantic=(
                SemanticMemoryQuery(
                    query="Find current enterprise policy context.",
                    max_items=2,
                    min_score=0.5,
                    timeout_ms=500,
                )
                if all_sources
                else SemanticMemoryRead(enabled=False)
            ),
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
        chat_session_id=session_id,
        chat_turn_id="turn-1",
        creation_reason="explicit_user_task_request",
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
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK,
        created_at=NOW,
        updated_at=NOW,
        pipeline_version="2",
        model_id=None,
        prompt_version=None,
        confidence=None,
    )


def _chat_summary(
    *, tenant_id: str = "tenant-1", session_id: str = "session-1"
) -> ChatSummaryEpisode:
    return ChatSummaryEpisode(
        episode_id="chat-summary-1",
        record_id="record-1",
        tenant_id=tenant_id,
        user_id="user@example.com",
        chat_session_id=session_id,
        chat_turn_id="turn-1",
        summary="The user asked for help prioritizing the approved procedure.",
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        retrieval_eligible=False,
        source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY,
        created_at=NOW,
        updated_at=NOW,
        expires_at=None,
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
        self.calls: list[tuple[MemoryNamespace, EpisodicMemoryQuery]] = []
        self.writes: list[tuple[MemoryNamespace, ChatSummaryEpisode]] = []
        self.task_writes: list[tuple[MemoryNamespace, TaskEpisode, datetime | None]] = []
        self.transitions: list[EpisodeTransition] = []
        self.task_deletes: list[tuple[MemoryNamespace, str]] = []
        self.transition_result: TaskEpisode | None = None
        self.task_delete_results: list[bool] = []
        self.deletes: list[MemoryNamespace] = []

    async def read_episodes(
        self, namespace: MemoryNamespace, query: EpisodicMemoryQuery
    ) -> tuple[TaskEpisode, ...]:
        self.calls.append((namespace, query))
        return self.episodes

    async def write_chat_summary(
        self, namespace: MemoryNamespace, episode: ChatSummaryEpisode
    ) -> ChatSummaryEpisode:
        self.writes.append((namespace, episode))
        return episode

    async def write_task_episode(
        self,
        namespace: MemoryNamespace,
        episode: TaskEpisode,
        *,
        expires_at: datetime | None,
    ) -> TaskEpisode:
        self.task_writes.append((namespace, episode, expires_at))
        return episode

    async def transition_task_episode(self, transition: EpisodeTransition) -> TaskEpisode | None:
        self.transitions.append(transition)
        return self.transition_result

    async def delete_task_episode(
        self, namespace: MemoryNamespace, *, episode_id: str
    ) -> bool:
        self.task_deletes.append((namespace, episode_id))
        return self.task_delete_results.pop(0) if self.task_delete_results else False

    async def delete_chat_summary(self, namespace: MemoryNamespace) -> bool:
        self.deletes.append(namespace)
        return True


class SemanticReader:
    def __init__(self, context: Mapping[str, object] | None = None) -> None:
        self.context = context
        self.calls: list[tuple[MemoryNamespace, SemanticMemoryQuery]] = []

    async def read_semantic_context(
        self, namespace: MemoryNamespace, query: SemanticMemoryQuery
    ) -> Mapping[str, object] | None:
        self.calls.append((namespace, query))
        return self.context


class UnavailableEpisodeReader(EpisodeReader):
    async def read_episodes(
        self, namespace: MemoryNamespace, query: EpisodicMemoryQuery
    ) -> tuple[TaskEpisode, ...]:
        del namespace, query
        raise MemorySourceUnavailableError("episodic provider detail")


class UnavailableSemanticReader(SemanticReader):
    async def read_semantic_context(
        self, namespace: MemoryNamespace, query: SemanticMemoryQuery
    ) -> Mapping[str, object] | None:
        del namespace, query
        raise MemorySourceUnavailableError("semantic provider detail")


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


class _FailingProfileConnection:
    def __init__(self, error_type: type[Exception] = psycopg.OperationalError) -> None:
        self._error_type = error_type

    async def execute(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise self._error_type("database unavailable")


class _FailingProfileConnectionContext:
    def __init__(self, error_type: type[Exception] = psycopg.OperationalError) -> None:
        self._error_type = error_type

    async def __aenter__(self) -> _FailingProfileConnection:
        return _FailingProfileConnection(self._error_type)

    async def __aexit__(self, *args: object) -> None:
        del args


class _FailingProfilePool:
    def __init__(self, error_type: type[Exception] = psycopg.OperationalError) -> None:
        self._error_type = error_type

    def connection(self) -> _FailingProfileConnectionContext:
        return _FailingProfileConnectionContext(self._error_type)


def _gateway(
    *,
    profile_reader: ProfileReader | UnavailableProfileReader | None = None,
    episode_reader: EpisodeReader | None = None,
    semantic_reader: SemanticReader | None = None,
    **kwargs: object,
) -> MemoryGateway:
    return MemoryGateway(
        scope=_scope(),
        session_buffer=InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60),
        declarative_memory=profile_reader,
        episodic_memory=episode_reader,
        semantic_memory=semantic_reader,
        **kwargs,
    )


def test_gateway_fails_closed_on_lifecycle_and_truncates_an_overreturn() -> None:
    approved = _episode(episode_id="approved", status=ValidationStatus.USER_APPROVED)
    completed = _episode(episode_id="completed", status=ValidationStatus.COMPLETED)
    unvalidated = _episode(
        episode_id="unvalidated", status=ValidationStatus.SYSTEM_GENERATED
    )
    object.__setattr__(unvalidated, "retrieval_eligible", True)
    rejected = _episode(episode_id="rejected", status=ValidationStatus.REJECTED)
    object.__setattr__(rejected, "retrieval_eligible", True)
    extra = _episode(episode_id="extra", status=ValidationStatus.USER_APPROVED)
    profiles = ProfileReader(_profile())
    episodes = EpisodeReader((unvalidated, rejected, approved, completed, extra))
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
    assert episodes.calls[0][1] == _request(_scope()).reads.episodic
    assert semantic.calls[0][1] == _request(_scope()).reads.semantic


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


def test_postgres_profile_read_failure_degrades_at_the_gateway_boundary() -> None:
    repository = PostgresChatProfileRepository(_FailingProfilePool())  # type: ignore[arg-type]

    response = asyncio.run(
        _gateway(profile_reader=repository).read_context(_request(_scope()))
    )

    assert response.profile is None
    assert response.degraded_sources == (
        DegradedMemorySource.LONG_TERM,
        DegradedMemorySource.EPISODIC,
        DegradedMemorySource.SEMANTIC,
    )
    assert "database unavailable" not in str(response.to_dict())


def test_postgres_profile_read_preserves_programming_errors() -> None:
    repository = PostgresChatProfileRepository(
        _FailingProfilePool(psycopg.ProgrammingError)  # type: ignore[arg-type]
    )
    namespace = MemoryNamespace(
        scope=_scope(),
        memory_type=MemoryType.LONG_TERM,
        record_id=None,
        source_id=None,
    )

    with pytest.raises(psycopg.ProgrammingError):
        asyncio.run(repository.read_profile(namespace))


def _explicit_provenance(
    *, source_type: MemoryProvenanceSource = MemoryProvenanceSource.EXPLICIT_USER_CONFIG
) -> MemoryProvenance:
    return MemoryProvenance(
        source_type=source_type,
        source_id="chat-settings-form",
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
                _profile(),
                provenance=_explicit_provenance(
                    source_type=MemoryProvenanceSource.SYSTEM_GENERATED_CHAT_TASK
                ),
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


def test_delete_all_memory_is_exact_scope_retryable_and_never_calls_semantic() -> None:
    class Profiles:
        def __init__(self) -> None:
            self.calls = 0

        async def delete_profile(self, namespace: MemoryNamespace) -> bool:
            assert namespace.tenant_id == "tenant-1"
            self.calls += 1
            return self.calls == 1

    class Episodes:
        def __init__(self) -> None:
            self.calls = 0

        async def delete_all_for_user(self, namespace: MemoryNamespace) -> int:
            assert namespace.tenant_id == "tenant-1"
            assert namespace.user_id == "user@example.com"
            assert namespace.feature == "ai_chat"
            self.calls += 1
            return 2 if self.calls == 1 else 0

    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    gateway = MemoryGateway(
        scope=_scope(), session_buffer=buffer, declarative_memory=Profiles(),
        episodic_memory=Episodes(),
    )
    gateway.append_turn(_turn())

    first = asyncio.run(gateway.delete_all_memory())
    second = asyncio.run(gateway.delete_all_memory())

    assert first.complete is second.complete is True
    assert first.episodic_deleted_count == 2
    assert second.episodic_deleted_count == 0
    assert buffer.read(gateway._namespace(MemoryType.SHORT_TERM)) == ()


def test_delete_all_memory_fails_closed_when_a_durable_adapter_is_missing() -> None:
    with pytest.raises(MemorySourceUnavailableError):
        asyncio.run(_gateway().delete_all_memory())


def test_observability_sink_failure_and_huge_clock_do_not_change_read_semantics() -> None:
    class RaisingSink:
        def emit(self, event: object) -> None:
            del event
            raise RuntimeError("sink failure must not escape")

    profiles = ProfileReader(_profile())
    gateway = _gateway(
        profile_reader=profiles,
        memory_operation_sink=RaisingSink(),
        monotonic_clock=lambda: 10**100,
    )

    response = asyncio.run(gateway.read_context(_request(_scope())))

    assert response.profile == _profile()


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


@pytest.mark.parametrize(
    ("episodic_enabled", "semantic_enabled"),
    [(True, False), (False, True)],
    ids=["episodic_only", "semantic_only"],
)
def test_gateway_calls_only_the_independently_selected_optional_source(
    episodic_enabled: bool, semantic_enabled: bool
) -> None:
    episodes = EpisodeReader(())
    semantic = SemanticReader({"citation_ids": ["doc-1#0"]})
    reads = MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=(
            EpisodicMemoryQuery(
                query="prior task",
                max_items=2,
                min_score=0.5,
                timeout_ms=500,
            )
            if episodic_enabled
            else EpisodicMemoryRead(
                enabled=False, retrieval_eligible_only=True, max_items=1
            )
        ),
        semantic=(
            SemanticMemoryQuery(
                query="company policy",
                max_items=2,
                min_score=0.5,
                timeout_ms=500,
            )
            if semantic_enabled
            else SemanticMemoryRead(enabled=False)
        ),
    )
    request = MemoryContextRequest(
        session_id="session-1", scope=_scope(), reads=reads
    )

    response = asyncio.run(
        _gateway(
            profile_reader=ProfileReader(_profile()),
            episode_reader=episodes,
            semantic_reader=semantic,
        ).read_context(request)
    )

    assert bool(episodes.calls) is episodic_enabled
    assert bool(semantic.calls) is semantic_enabled
    assert response.degraded is False


def test_episodic_unavailability_degrades_only_episodic_and_preserves_semantic() -> None:
    response = asyncio.run(
        _gateway(
            profile_reader=ProfileReader(_profile()),
            episode_reader=UnavailableEpisodeReader(()),
            semantic_reader=SemanticReader({"citation_ids": ["doc-1#0"]}),
        ).read_context(_request(_scope()))
    )

    assert response.episodes == ()
    assert response.semantic_context == {"citation_ids": ("doc-1#0",)}
    assert response.degraded_sources == (DegradedMemorySource.EPISODIC,)


def test_semantic_unavailability_degrades_only_semantic_and_preserves_episodes() -> None:
    approved = _episode(episode_id="approved", status=ValidationStatus.USER_APPROVED)
    response = asyncio.run(
        _gateway(
            profile_reader=ProfileReader(_profile()),
            episode_reader=EpisodeReader((approved,)),
            semantic_reader=UnavailableSemanticReader(),
        ).read_context(_request(_scope()))
    )

    assert response.episodes == (approved,)
    assert response.semantic_context is None
    assert response.degraded_sources == (DegradedMemorySource.SEMANTIC,)


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


def test_gateway_exposes_only_authorized_durable_write_operations() -> None:
    public_methods = {
        name
        for name, value in vars(MemoryGateway).items()
        if callable(value) and not name.startswith("_")
    }

    assert public_methods == {
        "append_turn",
        "read_context",
        "clear_session",
        "write_profile",
        "delete_profile",
        "write_chat_summary",
        "write_task_episode",
        "transition_task_episode",
        "delete_task_episode",
        "delete_chat_summary",
        "delete_all_memory",
    }


def test_gateway_writes_a_system_generated_chat_summary_to_its_exact_namespace() -> None:
    episodes = EpisodeReader(())
    episode = _chat_summary()

    written = asyncio.run(_gateway(episode_reader=episodes).write_chat_summary(episode))

    assert written == episode
    assert episodes.writes == [
        (
            MemoryNamespace(
                scope=_scope(),
                memory_type=MemoryType.EPISODIC,
                record_id=episode.record_id,
                source_id=episode.chat_turn_id,
            ),
            episode,
        )
    ]


@pytest.mark.parametrize(
    "episode",
    [
        _chat_summary(tenant_id="tenant-2"),
        ChatSummaryEpisode(
            episode_id="chat-summary-user-2",
            record_id="record-1",
            tenant_id="tenant-1",
            user_id="other@example.com",
            chat_session_id="session-1",
            chat_turn_id="turn-1",
            summary="The user asked for help prioritizing the approved procedure.",
            validation_status=ValidationStatus.SYSTEM_GENERATED,
            retrieval_eligible=False,
            source_type=EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY,
            created_at=NOW,
            updated_at=NOW,
            expires_at=None,
            pipeline_version="2",
            model_id=None,
            prompt_version=None,
            confidence=None,
        ),
        _chat_summary(session_id="session-2"),
    ],
    ids=["foreign_tenant", "foreign_user", "foreign_session"],
)
def test_gateway_rejects_foreign_chat_summary_before_adapter_write(
    episode: ChatSummaryEpisode,
) -> None:
    episodes = EpisodeReader(())

    with pytest.raises(NamespaceAccessDenied):
        asyncio.run(_gateway(episode_reader=episodes).write_chat_summary(episode))

    assert episodes.writes == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("validation_status", ValidationStatus.USER_APPROVED),
        ("retrieval_eligible", True),
        ("source_type", EpisodeSourceType.SYSTEM_GENERATED_CHAT_TASK),
    ],
    ids=["validation_status", "retrieval_eligible", "source_type"],
)
def test_gateway_rejects_forged_summary_provenance_before_adapter_write(
    field: str, value: object
) -> None:
    episodes = EpisodeReader(())
    episode = _chat_summary()
    object.__setattr__(episode, field, value)

    with pytest.raises(ChatSummaryWriteRejected, match="invalid bounded shape"):
        asyncio.run(_gateway(episode_reader=episodes).write_chat_summary(episode))

    assert episodes.writes == []


def test_gateway_requires_an_episodic_adapter_for_a_requested_summary_write() -> None:
    with pytest.raises(MemorySourceUnavailableError, match="episodic"):
        asyncio.run(_gateway().write_chat_summary(_chat_summary()))


def test_gateway_deletes_only_the_in_scope_chat_summary_record() -> None:
    episodes = EpisodeReader(())

    deleted = asyncio.run(
        _gateway(episode_reader=episodes).delete_chat_summary("record-to-delete")
    )

    assert deleted is True
    assert episodes.deletes == [
        MemoryNamespace(
            scope=_scope(),
            memory_type=MemoryType.EPISODIC,
            record_id="record-to-delete",
            source_id=None,
        )
    ]


def test_gateway_requires_an_episodic_adapter_for_summary_deletion() -> None:
    with pytest.raises(MemorySourceUnavailableError, match="episodic"):
        asyncio.run(_gateway().delete_chat_summary("record-to-delete"))


def test_gateway_writes_a_task_episode_to_its_exact_namespace_and_forwards_expiry() -> None:
    episodes = EpisodeReader(())
    episode = _episode(episode_id="task-write", status=ValidationStatus.SYSTEM_GENERATED)
    expires_at = NOW + timedelta(days=1)
    gateway = _gateway(episode_reader=episodes)

    assert asyncio.run(gateway.write_task_episode(episode, expires_at=expires_at)) == episode
    assert asyncio.run(gateway.write_task_episode(episode, expires_at=expires_at)) == episode
    assert episodes.task_writes == [
        (
            MemoryNamespace(
                scope=_scope(),
                memory_type=MemoryType.EPISODIC,
                record_id=episode.record_id,
                source_id=episode.chat_turn_id,
            ),
            episode,
            expires_at,
        ),
        (
            MemoryNamespace(
                scope=_scope(),
                memory_type=MemoryType.EPISODIC,
                record_id=episode.record_id,
                source_id=episode.chat_turn_id,
            ),
            episode,
            expires_at,
        ),
    ]


def test_gateway_dispatches_the_reconstructed_task_episode_not_a_disguised_subclass() -> None:
    benign = _episode(episode_id="task-canonical", status=ValidationStatus.SYSTEM_GENERATED)

    class DisguisedTaskEpisode(TaskEpisode):
        def to_dict(self) -> dict[str, object]:
            return benign.to_dict()

    disguised = DisguisedTaskEpisode.from_dict(benign.to_dict())
    object.__setattr__(disguised, "task_title", "Tampered task title")
    episodes = EpisodeReader(())

    result = asyncio.run(
        _gateway(episode_reader=episodes).write_task_episode(disguised, expires_at=None)
    )

    received = episodes.task_writes[0][1]
    assert type(received) is TaskEpisode
    assert received == benign
    assert result is received


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant-2"),
        ("user_id", "other@example.com"),
        ("chat_session_id", "session-2"),
    ],
    ids=["tenant", "user", "session"],
)
def test_gateway_rejects_foreign_task_episode_before_adapter_write(
    field: str, value: str
) -> None:
    episodes = EpisodeReader(())
    episode = _episode(episode_id="foreign-task", status=ValidationStatus.SYSTEM_GENERATED)
    object.__setattr__(episode, field, value)

    with pytest.raises(NamespaceAccessDenied):
        asyncio.run(_gateway(episode_reader=episodes).write_task_episode(episode, expires_at=None))

    assert episodes.task_writes == []


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime(2026, 8, 11, 9),
        NOW,
        "2026-08-11T09:00:00+00:00",
    ],
    ids=["naive", "equal_to_created_at", "not_datetime"],
)
def test_gateway_rejects_an_invalid_task_episode_expiry_before_adapter_write(
    expires_at: object,
) -> None:
    episodes = EpisodeReader(())
    episode = _episode(episode_id="task-expiry", status=ValidationStatus.SYSTEM_GENERATED)

    with pytest.raises(TaskEpisodeWriteRejected):
        asyncio.run(
            _gateway(episode_reader=episodes).write_task_episode(
                episode, expires_at=expires_at
            )
        )

    assert episodes.task_writes == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("creation_reason", "implicit"),
        ("validation_status", ValidationStatus.USER_APPROVED),
        ("retrieval_eligible", True),
        ("source_type", EpisodeSourceType.SYSTEM_GENERATED_CHAT_SUMMARY),
        ("action_plan", ("x" * 501,)),
        ("rag_citations", ({"raw_email": "copied"},)),
    ],
    ids=["creation_reason", "status", "eligible", "source_type", "unbounded", "raw_shaped"],
)
def test_gateway_rejects_unauthorized_task_episode_before_adapter_write(
    field: str, value: object
) -> None:
    episodes = EpisodeReader(())
    episode = _episode(episode_id="task-forged", status=ValidationStatus.SYSTEM_GENERATED)
    object.__setattr__(episode, field, value)

    with pytest.raises(TaskEpisodeWriteRejected):
        asyncio.run(_gateway(episode_reader=episodes).write_task_episode(episode, expires_at=None))

    assert episodes.task_writes == []


def test_gateway_requires_an_episodic_adapter_for_a_task_episode_write() -> None:
    with pytest.raises(MemorySourceUnavailableError, match="episodic"):
        asyncio.run(
            _gateway().write_task_episode(
                _episode(episode_id="task-adapter", status=ValidationStatus.SYSTEM_GENERATED),
                expires_at=None,
            )
        )


def test_task_episode_transition_and_deletion_gateway_signatures_keep_authority_internal() -> None:
    transition_parameters = inspect.signature(MemoryGateway.transition_task_episode).parameters
    deletion_parameters = inspect.signature(MemoryGateway.delete_task_episode).parameters

    assert "namespace" not in transition_parameters
    assert "retrieval_eligible" not in transition_parameters
    assert "namespace" not in deletion_parameters
    assert set(transition_parameters) == {
        "self", "record_id", "chat_turn_id", "episode_id", "from_status", "to_status",
        "transitioned_at",
    }
    assert set(deletion_parameters) == {"self", "record_id", "chat_turn_id", "episode_id"}


@pytest.mark.parametrize(
    ("from_status", "to_status", "retrieval_eligible"),
    [
        (ValidationStatus.SYSTEM_GENERATED, ValidationStatus.USER_APPROVED, True),
        (ValidationStatus.SYSTEM_GENERATED, ValidationStatus.COMPLETED, True),
        (ValidationStatus.SYSTEM_GENERATED, ValidationStatus.REJECTED, False),
        (ValidationStatus.USER_APPROVED, ValidationStatus.COMPLETED, True),
        (ValidationStatus.USER_APPROVED, ValidationStatus.REJECTED, False),
    ],
)
def test_gateway_transitions_a_task_episode_with_exact_canonical_scope_and_eligibility(
    from_status: ValidationStatus, to_status: ValidationStatus, retrieval_eligible: bool
) -> None:
    episodes = EpisodeReader(())
    returned = _episode(episode_id="task-transition", status=to_status)
    episodes.transition_result = returned
    sink = RecordingMemoryOperationSink()
    transitioned_at = datetime(2026, 8, 11, 9, tzinfo=UTC)

    result = asyncio.run(
        _gateway(episode_reader=episodes, memory_operation_sink=sink).transition_task_episode(
            record_id="record-transition", chat_turn_id="turn-transition",
            episode_id="task-transition", from_status=from_status, to_status=to_status,
            transitioned_at=transitioned_at,
        )
    )

    expected_namespace = MemoryNamespace(
        scope=_scope(), memory_type=MemoryType.EPISODIC,
        record_id="record-transition", source_id="turn-transition",
    )
    assert result is returned
    assert len(episodes.transitions) == 1
    received = episodes.transitions[0]
    assert type(received) is EpisodeTransition
    assert received == EpisodeTransition(
        episode_id="task-transition", namespace=expected_namespace,
        from_status=from_status, to_status=to_status,
        retrieval_eligible=retrieval_eligible, transitioned_at=transitioned_at,
    )
    assert [(event.operation, event.outcome, event.result_count) for event in sink.events] == [
        (MemoryOperation.WRITE, MemoryOutcome.REQUESTED, 0),
        (MemoryOperation.WRITE, MemoryOutcome.SUCCESS, 1),
    ]


def test_gateway_treats_a_missing_task_transition_as_a_safe_idempotent_miss() -> None:
    episodes = EpisodeReader(())
    sink = RecordingMemoryOperationSink()

    result = asyncio.run(
        _gateway(episode_reader=episodes, memory_operation_sink=sink).transition_task_episode(
            record_id="record-transition",
            chat_turn_id="turn-transition",
            episode_id="missing-task",
            from_status=ValidationStatus.SYSTEM_GENERATED,
            to_status=ValidationStatus.REJECTED,
            transitioned_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
        )
    )

    assert result is None
    assert [(event.operation, event.outcome, event.result_count) for event in sink.events] == [
        (MemoryOperation.WRITE, MemoryOutcome.REQUESTED, 0),
        (MemoryOperation.WRITE, MemoryOutcome.SUCCESS, 0),
    ]


@pytest.mark.parametrize(
    ("record_id", "chat_turn_id", "episode_id"),
    [("", "turn", "episode"), ("record/with-slash", "turn", "episode"),
     ("record", "", "episode"), ("record", "turn", "")],
    ids=["empty_record", "slashed_record", "empty_turn", "empty_episode"],
)
def test_gateway_rejects_invalid_task_transition_identity_before_adapter(
    record_id: str, chat_turn_id: str, episode_id: str
) -> None:
    episodes = EpisodeReader(())

    with pytest.raises((ValueError, TaskEpisodeTransitionRejected)):
        asyncio.run(
            _gateway(episode_reader=episodes).transition_task_episode(
                record_id=record_id, chat_turn_id=chat_turn_id, episode_id=episode_id,
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                transitioned_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            )
        )

    assert episodes.transitions == []


@pytest.mark.parametrize(
    ("from_status", "to_status", "transitioned_at"),
    [
        (
            ValidationStatus.COMPLETED,
            ValidationStatus.REJECTED,
            datetime(2026, 8, 11, 9, tzinfo=UTC),
        ),
        (
            ValidationStatus.SYSTEM_GENERATED,
            ValidationStatus.USER_APPROVED,
            datetime(2026, 8, 11, 9),
        ),
        (
            ValidationStatus.SYSTEM_GENERATED,
            ValidationStatus.USER_APPROVED,
            "2026-08-11T09:00:00+00:00",
        ),
    ],
    ids=["invalid_status", "naive_time", "invalid_time_type"],
)
def test_gateway_rejects_invalid_task_transition_values_before_adapter(
    from_status: ValidationStatus, to_status: ValidationStatus, transitioned_at: object
) -> None:
    episodes = EpisodeReader(())

    with pytest.raises(TaskEpisodeTransitionRejected):
        asyncio.run(
            _gateway(episode_reader=episodes).transition_task_episode(
                record_id="record-transition",
                chat_turn_id="turn-transition",
                episode_id="task-transition",
                from_status=from_status,
                to_status=to_status,
                transitioned_at=transitioned_at,  # type: ignore[arg-type]
            )
        )

    assert episodes.transitions == []


def test_gateway_deletes_only_the_exact_originating_task_episode_and_is_idempotent() -> None:
    episodes = EpisodeReader(())
    episodes.task_delete_results = [True, False]
    sink = RecordingMemoryOperationSink()
    gateway = _gateway(episode_reader=episodes, memory_operation_sink=sink)

    assert asyncio.run(
        gateway.delete_task_episode(
            record_id="record-delete", chat_turn_id="turn-delete", episode_id="task-delete"
        )
    )
    assert not asyncio.run(
        gateway.delete_task_episode(
            record_id="record-delete", chat_turn_id="turn-delete", episode_id="task-delete"
        )
    )
    expected = (
        MemoryNamespace(
            scope=_scope(), memory_type=MemoryType.EPISODIC,
            record_id="record-delete", source_id="turn-delete",
        ),
        "task-delete",
    )
    assert episodes.task_deletes == [expected, expected]
    assert [(event.operation, event.outcome, event.result_count) for event in sink.events] == [
        (MemoryOperation.DELETE, MemoryOutcome.REQUESTED, 0),
        (MemoryOperation.DELETE, MemoryOutcome.SUCCESS, 1),
        (MemoryOperation.DELETE, MemoryOutcome.REQUESTED, 0),
        (MemoryOperation.DELETE, MemoryOutcome.SUCCESS, 0),
    ]


@pytest.mark.parametrize(
    ("record_id", "chat_turn_id", "episode_id"),
    [("", "turn", "episode"), ("record/with-slash", "turn", "episode"),
     ("record", "", "episode"), ("record", "turn", ""),
     ("record", "turn", "   ")],
    ids=["empty_record", "slashed_record", "empty_turn", "empty_episode", "whitespace_episode"],
)
def test_gateway_rejects_invalid_task_episode_deletion_identity_before_adapter(
    record_id: str, chat_turn_id: str, episode_id: str
) -> None:
    episodes = EpisodeReader(())
    sink = RecordingMemoryOperationSink()

    with pytest.raises((ValueError, TaskEpisodeTransitionRejected)):
        asyncio.run(
            _gateway(episode_reader=episodes, memory_operation_sink=sink).delete_task_episode(
                record_id=record_id, chat_turn_id=chat_turn_id, episode_id=episode_id
            )
        )

    assert episodes.task_deletes == []
    assert sink.events == ()


def test_gateway_requires_an_episodic_adapter_for_task_episode_lifecycle() -> None:
    with pytest.raises(MemorySourceUnavailableError, match="episodic"):
        asyncio.run(
            _gateway().transition_task_episode(
                record_id="record-transition", chat_turn_id="turn-transition",
                episode_id="task-transition", from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                transitioned_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            )
        )
    with pytest.raises(MemorySourceUnavailableError, match="episodic"):
        asyncio.run(
            _gateway().delete_task_episode(
                record_id="record-delete", chat_turn_id="turn-delete", episode_id="task-delete"
            )
        )


def test_gateway_propagates_task_lifecycle_adapter_errors_without_success_telemetry() -> None:
    class FailingEpisodeReader(EpisodeReader):
        async def transition_task_episode(
            self, transition: EpisodeTransition
        ) -> TaskEpisode | None:
            del transition
            raise MemorySourceUnavailableError("episodic provider detail")

        async def delete_task_episode(
            self, namespace: MemoryNamespace, *, episode_id: str
        ) -> bool:
            del namespace, episode_id
            raise RuntimeError("adapter error")

    episodes = FailingEpisodeReader(())
    transition_sink = RecordingMemoryOperationSink()
    deletion_sink = RecordingMemoryOperationSink()

    with pytest.raises(MemorySourceUnavailableError, match="episodic provider detail"):
        asyncio.run(
            _gateway(episode_reader=episodes, memory_operation_sink=transition_sink)
            .transition_task_episode(
                record_id="record-transition", chat_turn_id="turn-transition",
                episode_id="task-transition", from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                transitioned_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            )
        )
    with pytest.raises(RuntimeError, match="adapter error"):
        asyncio.run(
            _gateway(episode_reader=episodes, memory_operation_sink=deletion_sink)
            .delete_task_episode(
                record_id="record-delete", chat_turn_id="turn-delete", episode_id="task-delete"
            )
        )

    assert [(event.operation, event.outcome) for event in transition_sink.events] == [
        (MemoryOperation.WRITE, MemoryOutcome.REQUESTED)
    ]
    assert [(event.operation, event.outcome) for event in deletion_sink.events] == [
        (MemoryOperation.DELETE, MemoryOutcome.REQUESTED)
    ]
