"""Deterministic Task Candidate correlation (master-comparison §7, rule 3).

Implements frozen cardinality rule 3 of
``docs/references/task-candidate-correlation-contract.md``: application-owned
deterministic logic (no LLM call) correlates Route Decisions by thread and
forms Task Candidates while preserving ``source_message_ids``; same input
decisions yield same candidates, in the same order.

V1-M2 scope (``tasks/plan.md`` T2.3): incident grouping is thread-scoped
only, so every Task Candidate carries ``incident_key=None``. Cross-thread
incident merge returns with the Generator in V1-M3, which is where
``incident_key`` originates in the legacy pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cowork_agent.domain.target_contracts import (
    EmailRouteDecision,
    EphemeralEmailEnvelope,
)


@dataclass(frozen=True, slots=True)
class TaskCandidate:
    """One Task Candidate: the correlated Route Decisions of one Gmail thread.

    The Task Candidate is the execution unit of the downstream Route Resolver
    and Generator stages (contract rules 4 and 6): each candidate resolves
    exactly once to one route, and each resolved non-``no_action`` candidate
    receives at most one generator call.

    ``incident_key`` is ``None`` for all candidates in V1-M2: incident
    grouping is thread-scoped only at this milestone. Cross-thread incident
    merge is deferred to V1-M3 generator output handling, where
    ``incident_key`` originates; until then the Gmail thread id is the stable
    correlation key.
    """

    #: Stable correlation key; the Gmail thread id in V1-M2.
    candidate_key: str
    gmail_thread_id: str
    incident_key: str | None
    #: Gmail message ids that contributed to the candidate, in fetch order.
    source_message_ids: tuple[str, ...]
    #: Each selected email's message id paired with its Route Decision, in
    #: the same order as ``source_message_ids``.
    decisions: tuple[tuple[str, EmailRouteDecision], ...]


def correlate_candidates(
    decisions: Mapping[str, EmailRouteDecision],
    envelopes: Mapping[str, EphemeralEmailEnvelope],
) -> tuple[TaskCandidate, ...]:
    """Correlate paired Route Decisions into Task Candidates by Gmail thread.

    Exactly one Task Candidate per thread of selected emails; every paired
    Route Decision appears in exactly one candidate; empty input yields zero
    candidates (frozen contract rule 3).

    Args:
        decisions: One Route Decision per selected email, keyed by Gmail
            message id. Exactly one decision per selected email is enforced
            by the caller (contract rule 2); this function enforces pairing.
        envelopes: Ephemeral Envelopes of the same selected emails, keyed by
            Gmail message id. Insertion order defines fetch order.

    Returns:
        Task Candidates in first-seen envelope order; within a candidate,
        ``source_message_ids`` and ``decisions`` preserve envelope insertion
        (fetch) order.

    Raises:
        ValueError: If a Gmail message id has a Route Decision without an
            Ephemeral Envelope, or an envelope without a Route Decision. The
            message names every unpaired id.

    Determinism: pure logic with no I/O, clock, or randomness; identical
    inputs always produce identical output.
    """
    unpaired_ids = sorted(set(decisions) ^ set(envelopes))
    if unpaired_ids:
        raise ValueError(
            "Every selected email must pair one Route Decision with one Ephemeral "
            f"Envelope; unpaired Gmail message id(s): {', '.join(unpaired_ids)}"
        )

    members_by_thread: dict[str, list[tuple[str, EmailRouteDecision]]] = {}
    for message_id, envelope in envelopes.items():
        members_by_thread.setdefault(envelope.gmail_thread_id, []).append(
            (message_id, decisions[message_id])
        )

    return tuple(
        TaskCandidate(
            candidate_key=thread_id,
            gmail_thread_id=thread_id,
            incident_key=None,
            source_message_ids=tuple(message_id for message_id, _ in members),
            decisions=tuple(members),
        )
        for thread_id, members in members_by_thread.items()
    )
