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

SCHEMA_VERSION = "2.0.0"
_MAX_ID_LENGTH = 64


class ProbeSetError(ValueError):
    """The probe set is not loadable as specified."""


class ProbeTest(StrEnum):
    """What kind of failure this probe is designed to catch (SPEC §4.2)."""

    RECALL = "recall"
    UPDATE = "update"
    RESTRAINT = "restraint"


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
    stale_any: tuple[str, ...] = ()
    expect_refusal: bool = False
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
    if not isinstance(raw_target, str):
        raise ProbeSetError(f"probe {probe_id}: targets must be a string")
    try:
        targets = MemoryType(raw_target)
    except ValueError as error:
        raise ProbeSetError(
            f"probe {probe_id}: targets must be one of "
            f"{[member.value for member in MemoryType]}"
        ) from error

    raw_test = data.get("test")
    if not isinstance(raw_test, str):
        raise ProbeSetError(f"probe {probe_id}: test must be a string")
    try:
        test = ProbeTest(raw_test)
    except ValueError as error:
        raise ProbeSetError(f"probe {probe_id}: unknown test type") from error

    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ProbeSetError(f"probe {probe_id}: question must be a non-empty string")

    expect_any = _string_tuple(data.get("expect_any"), f"probe {probe_id}: expect_any")
    stale_any = _string_tuple(data.get("stale_any"), f"probe {probe_id}: stale_any")
    expect_refusal = _bool(data.get("expect_refusal"), f"probe {probe_id}: expect_refusal")

    # A probe with no expectation always passes, which is worse than no probe.
    if not (expect_any or expect_refusal):
        raise ProbeSetError(
            f"probe {probe_id}: must declare an expectation (expect_any or expect_refusal)"
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
        stale_any=stale_any,
        expect_refusal=expect_refusal,
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
        raise ProbeSetError(f"schema_version must be {SCHEMA_VERSION!r}, got {version!r}")
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
