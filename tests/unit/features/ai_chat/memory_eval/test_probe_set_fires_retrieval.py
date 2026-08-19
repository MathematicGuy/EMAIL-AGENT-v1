"""The committed probe set must actually reach the scopes it claims to target.

Episodic and semantic reads are cue-gated. A probe that targets one of those
scopes but carries no cue asks its question with the retrieval switched off, and
then reports the resulting empty answer as a memory failure. That is worse than
no probe: it looks like a finding.

This bit for real. The probe set was written in English while the assistant
answers only in Vietnamese; translating the questions without also translating
the cue lists would have turned four of the eight probes into silent no-ops.
These assertions are what make that a red test rather than a quiet regression.
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

_PROBE_SET = (
    Path(__file__).resolve().parents[5]
    / "evaluations"
    / "MEMORIES"
    / "probes"
    / "v1-four-scopes.json"
)


def _load() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(_PROBE_SET.read_text(encoding="utf-8"))
    return loaded


def _request(user_message: str) -> ChatMessageRequest:
    return ChatMessageRequest(
        session_id="session-probe",
        user_message=user_message,
        idempotency_key="idempotency-probe",
    )


def test_the_probe_set_is_found() -> None:
    # A wrong path would make every assertion below vacuously true.
    assert _load()["probes"], f"no probes found in {_PROBE_SET}"


@pytest.mark.parametrize("scope", ["episodic", "semantic"])
def test_cue_gated_probes_actually_fire_their_retrieval(scope: str) -> None:
    expected = EpisodicMemoryQuery if scope == "episodic" else SemanticMemoryQuery
    probes = [p for p in _load()["probes"] if p["targets"] == scope]
    assert probes, f"no {scope} probes to check"

    for probe in probes:
        reads = select_memory_reads(_request(probe["question"]))
        assert isinstance(getattr(reads, scope), expected), (
            f"{probe['id']} targets {scope} but its question carries no {scope} cue, "
            f"so the read never fires and the probe measures nothing: {probe['question']!r}"
        )


def test_episodic_seed_requests_are_accepted_as_explicit_task_requests() -> None:
    # If the phrasing policy rejects the seed, no episode is written and every
    # episodic probe reports amnesia against an empty store.
    entries = _load()["seed"]["episodic"]
    assert entries, "no episodic seed to check"

    for entry in entries:
        assert is_explicit_task_request(_request(entry["request"])), (
            f"episodic seed request would not create a task: {entry['request']!r}"
        )


def test_recall_probes_do_not_themselves_create_tasks() -> None:
    # A probe that reads as a task directive writes an episode as a side effect
    # of being asked, which contaminates every later arm in the same run.
    for probe in _load()["probes"]:
        assert not is_explicit_task_request(_request(probe["question"])), (
            f"{probe['id']} reads as an explicit task request, so asking it "
            f"writes an episode: {probe['question']!r}"
        )


def test_recall_expectations_exist_somewhere_in_the_seed() -> None:
    # A recall probe is only a memory question if the thing it expects was put
    # into memory. `sem_recall_01` expects a form code that exists in one line
    # of one corpus file; if that file is edited and the probe is not, the probe
    # silently becomes unanswerable and reports the product as amnesiac.
    #
    # This does NOT check the expectation is unguessable - nothing offline can.
    # That is what the never-filled arm is for, and it is how the previous
    # timezone and 'phê duyệt' expectations were caught.
    data = _load()
    seed = data["seed"]
    corpus_dir = Path(__file__).resolve().parents[5] / seed["semantic"]["corpus_dir"]
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
