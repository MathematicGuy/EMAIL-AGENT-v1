"""The three controller-driven seeding rituals (SPEC §6).

`long_term` already ships in `seeding.py` — it needs only the gateway. These
three need the controller, because the authorization step under test lives
there: a turn must be spoken to enter the buffer, and a task must be requested
and approved to become retrievable.

Every ritual returns a SeedOutcome instead of raising. A scope that fails to
seed is a finding about that scope (SPEC §6.1), and the other three still run.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from pathlib import Path

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatMessageRequest,
    EpisodicMemoryQuery,
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryQuery,
)
from cowork_agent.domain.target_contracts import (
    RetrievalStatus,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)
from cowork_agent.integrations.rag.chat_memory import SemanticChatMemoryAdapter
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.memory import InRepoSemanticMemory

from ..controller import ChatController
from ..memory_gateway import MemoryGateway
from .live_controller import ask_once, collect_errors, collect_reply
from .live_env import ScopeFinding
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
        return SeedOutcome.nothing_declared(MemoryType.SHORT_TERM)
    try:
        for index, line in enumerate(spec.short_term):
            await ask_once(controller, session_id, line, f"{key_prefix}-st-{index}")
    except Exception as error:  # noqa: BLE001 - a seed failure is a finding
        return SeedOutcome(MemoryType.SHORT_TERM, False, f"{type(error).__name__}: {error}")
    return SeedOutcome(MemoryType.SHORT_TERM, True, f"seeded {len(spec.short_term)} turns")


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
        return SeedOutcome.nothing_declared(MemoryType.EPISODIC)

    approved = 0
    try:
        for index, entry in enumerate(spec.episodic):
            request = ChatMessageRequest(session_id, entry.request, f"{key_prefix}-ep-{index}")
            events = [event async for event in controller.stream_message(request)]
            _, episode_ids = collect_reply(events)
            if not episode_ids:
                # Only the observed fact plus whatever the stream said. This
                # used to assert `is_explicit_task_request` had rejected the
                # phrasing, which it never checked — and for the shipped seed
                # that function returns True, so the report named a cause that
                # was not the cause.
                errors = collect_errors(events)
                detail = "; ".join(errors) if errors else "no error was reported"
                return SeedOutcome(
                    MemoryType.EPISODIC,
                    False,
                    f"no task episode was created for seed {index} ({detail}); "
                    "the turn produced no episodic citation to approve",
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
        return SeedOutcome.nothing_declared(MemoryType.SEMANTIC), None
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


class EmptySemanticMemory:
    """A corpus that was never indexed: the read fires and finds nothing.

    The control arm differs from the others by having no seed, never by having
    a read switched off (SPEC §5.1). For semantic, the "seed" is the index the
    read hits — so a control arm needs a semantic store that answers, and
    answers with nothing. Handing it `semantic_memory=None` instead would make
    the gateway fail closed, which is a disabled read wearing a control arm's
    name, and every semantic probe would look leaked no matter what the system
    did. `InRepoSemanticMemory` cannot serve here: it refuses an empty corpus
    at construction, by design.
    """

    async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
        return SemanticRetrievalResponse(
            query_id=request.run_id,
            chunks=(),
            retrieval_status=RetrievalStatus.NO_RESULTS,
            latency_ms=0,
        )


def _verification_reads(episodic_query: str | None) -> MemoryReadOptions:
    """Read every scope, masked by nothing. Verification must see the truth.

    `episodic_query` is supplied by the caller because no query text is right in
    the abstract. It used to be the literal "previous task", which matched no
    row of a Vietnamese corpus on any arm of any run, and the resulting empty
    read was reported as an empty store. `_episodic_finding` says what is passed
    instead.

    The semantic query stays fixed. Semantic retrieval is embedding similarity,
    which returns a nearest neighbour for any text rather than requiring a term
    to match, so the wording cannot make a populated index look empty.
    """

    return MemoryReadOptions(
        short_term=True,
        long_term=True,
        episodic=(
            EpisodicMemoryQuery(query=episodic_query, max_items=5, min_score=0.0, timeout_ms=2000)
            if episodic_query
            else EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=1)
        ),
        semantic=SemanticMemoryQuery(
            query="company policy", max_items=5, min_score=0.0, timeout_ms=2000
        ),
    )


async def verify_seed(
    gateway: MemoryGateway,
    scope: ChatMemoryScope,
    expected_scopes: Sequence[MemoryType],
) -> tuple[ScopeFinding, ...]:
    """Confirm each seeded scope actually reads back non-empty.

    Called on the `full` and `<target>_off` arms only. The `control` arm has no
    seed by definition, so verifying it would fail every scope every run.

    Our writes are transactional, so this is a single check rather than a poll.

    What this can and cannot say: `short_term` and `long_term` are fetched
    directly, so an empty read means an empty store. `episodic` and `semantic`
    are RETRIEVALS, and a store can hold a record that the query does not
    match. Episodic therefore asks the storage question separately, through
    `list_task_episodes`, which runs no search:

    - nothing stored        -> the write path failed. A harness finding.
    - stored, search empty  -> the write path worked and retrieval did not.
                               A product finding, and a different bug.

    Reporting those two as one line is what made all sixteen episodic failures
    unreadable: every one of them named an empty store, and the store was full.

    `semantic` keeps the single wording. It has no write path and no listing —
    "seeding" it means building the index the read hits — so there is no second
    question to ask.
    """

    if not expected_scopes:
        return ()
    request = MemoryContextRequest(
        session_id=scope.session_id, scope=scope, reads=_verification_reads(None)
    )
    try:
        # Called through MemoryGateway explicitly, not through `gateway`. On an
        # ablated arm the gateway IS an ArmScopedMemoryGateway, whose
        # read_context masks the target scope — and that mask is a statement
        # about what the PROBE may read, never about what the store holds.
        # Verifying through it read the ablated arm's own target back as empty
        # on every run, so a healthy store reported itself as amnesia.
        response = await MemoryGateway.read_context(gateway, request)
    except Exception as error:  # noqa: BLE001 - an unreadable store is a finding
        return tuple(
            ScopeFinding(item, f"verification read failed: {error}") for item in expected_scopes
        )

    populated = {
        MemoryType.SHORT_TERM: bool(response.turns),
        MemoryType.LONG_TERM: response.profile is not None,
        MemoryType.SEMANTIC: response.semantic_context is not None,
    }
    findings: list[ScopeFinding] = []
    for item in expected_scopes:
        if item is MemoryType.EPISODIC:
            episodic_finding = await _episodic_finding(gateway, scope)
            if episodic_finding is not None:
                findings.append(episodic_finding)
            continue
        if not populated[item]:
            findings.append(
                ScopeFinding(item, f"{item.value}: the verification read came back empty")
            )
    return tuple(findings)


async def _episodic_finding(gateway: MemoryGateway, scope: ChatMemoryScope) -> ScopeFinding | None:
    """Ask the storage question first, then the retrieval question separately.

    The retrieval question is asked with the stored episode's OWN title as the
    query. Every token of a title is in that row's search vector by
    construction, so this is the friendliest query that row will ever get. An
    empty result here cannot be blamed on wording, on language, or on the model
    having paraphrased the seed request: the row is there and the retrieval path
    will not return it.
    """

    try:
        # Called unbound for the same reason read_context is: on an ablated arm
        # the gateway masks its own target, and a mask is a statement about what
        # the PROBE may read, never about what the store holds.
        stored = await MemoryGateway.list_task_episodes(gateway)
    except Exception as error:  # noqa: BLE001 - an unlistable store is a finding
        return ScopeFinding(
            MemoryType.EPISODIC,
            f"episodic: the stored episodes could not be listed ({type(error).__name__}: {error})",
        )
    if not stored:
        return ScopeFinding(MemoryType.EPISODIC, "episodic: nothing was written to the store")

    title = stored[0].task_title
    request = MemoryContextRequest(
        session_id=scope.session_id, scope=scope, reads=_verification_reads(title)
    )
    try:
        response = await MemoryGateway.read_context(gateway, request)
    except Exception as error:  # noqa: BLE001 - an unreadable store is a finding
        return ScopeFinding(MemoryType.EPISODIC, f"episodic: verification read failed: {error}")
    if response.episodes:
        return None
    plural = "" if len(stored) == 1 else "s"
    return ScopeFinding(
        MemoryType.EPISODIC,
        f"episodic: {len(stored)} episode{plural} stored, and retrieval returned none of "
        f"them when queried with a stored title verbatim ({title!r}) - the write path "
        f"worked and the retrieval path did not",
    )
