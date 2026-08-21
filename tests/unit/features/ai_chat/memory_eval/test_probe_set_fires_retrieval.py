"""Every committed probe set must actually reach the scopes it claims to target.

Episodic and semantic reads are cue-gated. A probe that targets one of those
scopes but carries no cue asks its question with the retrieval switched off, and
then reports the resulting empty answer as a memory failure. That is worse than
no probe: it looks like a finding.

This bit for real. The probe set was written in English while the assistant
answers only in Vietnamese; translating the questions without also translating
the cue lists would have turned four of the eight probes into silent no-ops.
These assertions are what make that a red test rather than a quiet regression.

Every test here is parametrized over `probes/*.json` rather than over one named
file. A second probe set is data, and data that ships without these guards is
how a dead cue reaches a report as amnesia.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatMessageRequest,
    EpisodicMemoryQuery,
    SemanticMemoryQuery,
)
from cowork_agent.features.ai_chat.retrieval_policy import (
    is_explicit_task_request,
    select_memory_reads,
)

from .conftest import PROBE_SET_PATHS, REPO_ROOT


def _load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _request(user_message: str) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id="session-probe",
        user_message=user_message,
        idempotency_key="idempotency-probe",
    )


def test_probe_sets_are_found() -> None:
    # Discovery replaced a hardcoded path, and an empty glob would make every
    # parametrized assertion below vacuously true by generating no cases at all.
    # This is the one test that must not take the probe set as a parameter.
    assert PROBE_SET_PATHS, "no probe sets found under evaluations/MEMORIES/probes"


def test_the_probe_set_is_found(probe_set_path: Path) -> None:
    assert _load(probe_set_path)["probes"], f"no probes found in {probe_set_path}"


@pytest.mark.parametrize("scope", ["episodic", "semantic"])
def test_cue_gated_probes_actually_fire_their_retrieval(scope: str, probe_set_path: Path) -> None:
    expected = EpisodicMemoryQuery if scope == "episodic" else SemanticMemoryQuery
    probes = [p for p in _load(probe_set_path)["probes"] if p["targets"] == scope]
    assert probes, f"no {scope} probes to check in {probe_set_path.name}"

    for probe in probes:
        reads = select_memory_reads(_request(probe["question"]))
        assert isinstance(getattr(reads, scope), expected), (
            f"{probe['id']} targets {scope} but its question carries no {scope} cue, "
            f"so the read never fires and the probe measures nothing: {probe['question']!r}"
        )


def test_episodic_seed_requests_are_accepted_as_explicit_task_requests(
    probe_set_path: Path,
) -> None:
    # If the phrasing policy rejects the seed, no episode is written and every
    # episodic probe reports amnesia against an empty store.
    entries = _load(probe_set_path)["seed"]["episodic"]
    assert entries, f"no episodic seed to check in {probe_set_path.name}"

    for entry in entries:
        assert is_explicit_task_request(_request(entry["request"])), (
            f"episodic seed request would not create a task: {entry['request']!r}"
        )


def test_recall_probes_do_not_themselves_create_tasks(probe_set_path: Path) -> None:
    # A probe that reads as a task directive writes an episode as a side effect
    # of being asked, which contaminates every later arm in the same run.
    for probe in _load(probe_set_path)["probes"]:
        assert not is_explicit_task_request(_request(probe["question"])), (
            f"{probe['id']} reads as an explicit task request, so asking it "
            f"writes an episode: {probe['question']!r}"
        )


def test_recall_expectations_exist_somewhere_in_the_seed(probe_set_path: Path) -> None:
    # A recall probe is only a memory question if the thing it expects was put
    # into memory. `sem_recall_01` expects a form code that exists in one line
    # of one corpus file; if that file is edited and the probe is not, the probe
    # silently becomes unanswerable and reports the product as amnesiac.
    #
    # This does NOT check the expectation is unguessable - nothing offline can.
    # That is what the never-filled arm is for, and it is how the previous
    # timezone and 'phê duyệt' expectations were caught.
    #
    # The corpus is read from THIS set's own seed. Probe sets do not share a
    # corpus - v2 has its own - so a constant here would grade one set's
    # expectations against another set's documents.
    data = _load(probe_set_path)
    seed = data["seed"]
    corpus_dir = REPO_ROOT / seed["semantic"]["corpus_dir"]
    material = "\n".join(
        [
            *seed["short_term"],
            *(str(value) for value in seed["long_term"].values()),
            *(entry["request"] for entry in seed["episodic"]),
            *(path.read_text(encoding="utf-8") for path in sorted(corpus_dir.iterdir())),
        ]
    ).casefold()

    for probe in data["probes"]:
        if probe["test"] != "recall":
            continue
        assert any(expected.casefold() in material for expected in probe["expect_any"]), (
            f"{probe['id']} expects {probe['expect_any']!r}, and none of those "
            f"appear anywhere in the seed - nothing put them in memory to recall"
        )


def test_invented_any_phrases_exist_somewhere_in_the_seed(probe_set_path: Path) -> None:
    # invented_any is a near-miss only if the neighbour was actually stored.
    # A phrase that appears nowhere in this set's seed is a random string, and
    # the grader cannot fairly call it invention-from-neighbour.
    # Same material concatenation as recall-expectation grounding: this set's
    # own corpus_dir, never a sibling set's documents.
    data = _load(probe_set_path)
    seed = data["seed"]
    corpus_dir = REPO_ROOT / seed["semantic"]["corpus_dir"]
    material = "\n".join(
        [
            *seed["short_term"],
            *(str(value) for value in seed["long_term"].values()),
            *(entry["request"] for entry in seed["episodic"]),
            *(path.read_text(encoding="utf-8") for path in sorted(corpus_dir.iterdir())),
        ]
    ).casefold()

    missing: list[str] = []
    for probe in data["probes"]:
        for phrase in probe.get("invented_any") or []:
            if phrase.casefold() not in material:
                missing.append(f"{probe['id']}:{phrase!r}")
    assert not missing, (
        f"{probe_set_path.name} invented_any phrases absent from seed: {missing}"
    )
