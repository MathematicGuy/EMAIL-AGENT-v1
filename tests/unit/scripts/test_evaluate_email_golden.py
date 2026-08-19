"""Tests for immutable, metadata-only Email Intent evaluation runs."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EmailRouteDecision,
    FetchStatus,
    ReasonCode,
    Route,
)
from cowork_agent.features.email_action_plan.schemas import ClassificationResult, ClassifiedMessage
from tests.unit.scripts.cli_harness import load_script, run_cli

NOW = datetime(2026, 8, 19, tzinfo=UTC)
RUBRIC_VERSION = "email-intent-annotation-v1"


def load_module():
    return load_script("evaluate_email_golden")


def _ground_truth() -> dict[str, object]:
    return {
        "actionability": "action_required",
        "email_is_sufficient": False,
        "knowledge_gaps": ["Synthetic missing policy"],
        "expected_document_types": ["company_policy"],
        "expected_route": "retrieve_rag",
        "rationale": "Synthetic human-reviewed rationale.",
    }


def _candidate_case(index: int) -> dict[str, object]:
    return {
        "case_id": f"email_case_{index:03d}",
        "source_message_id": f"synthetic-message-{index:03d}",
        "gmail_thread_id": f"synthetic-thread-{index:03d}",
        "sender": "Synthetic Sender <synthetic@example.com>",
        "subject": f"Synthetic subject {index}",
        "received_at": "2026-08-19T00:00:00Z",
        "labels": ["INBOX"],
        "gmail_content": "Synthetic private body.",
    }


def candidates(case_count: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "fetched_at": "2026-08-19T00:00:00Z",
        "gmail_query": "in:inbox",
        "ordering": "received_at_desc",
        "case_count": case_count,
        "cases": [_candidate_case(index) for index in range(1, case_count + 1)],
    }


def golden(case_count: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "rubric_version": RUBRIC_VERSION,
        "case_count": case_count,
        "cases": [
            {
                "case_id": f"email_case_{index:03d}",
                "source_message_id": f"synthetic-message-{index:03d}",
                "ground_truth": _ground_truth(),
                "annotation": {
                    "source": "human_reviewed",
                    "rubric_version": RUBRIC_VERSION,
                    "reviewed_at": "2026-08-19T00:00:00Z",
                },
            }
            for index in range(1, case_count + 1)
        ],
    }


def _decision() -> EmailRouteDecision:
    return EmailRouteDecision(
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.RETRIEVE_RAG,
        candidate_action_item="Review the policy.",
        email_is_sufficient=False,
        knowledge_gaps=("Synthetic missing policy",),
        retrieval_query="synthetic policy",
        expected_document_types=(),
        reason_codes=(ReasonCode.POLICY_REQUIRED,),
        confidence=0.9,
    )


class FakeClassifier:
    async def classify(self, user_timezone, current_time, messages):
        del user_timezone, current_time
        return ClassificationResult(
            tuple(ClassifiedMessage(message.gmail_message_id, _decision()) for message in messages),
            batch_count=1,
        )


def _summary(selected_candidates: list[dict[str, object]]) -> dict[str, object]:
    module = load_module()
    messages = module.load_envelopes_from_candidates(selected_candidates)
    return asyncio.run(module.evaluate(messages, FakeClassifier(), NOW))


def test_build_run_artifact_uses_explicit_shard_and_immutable_versions() -> None:
    module = load_module()
    candidate_dataset = candidates(200)
    golden_200 = golden(200)
    candidate_cases = candidate_dataset["cases"]
    assert isinstance(candidate_cases, list)
    selected = [dict(case) for case in candidate_cases[50:100]]

    run = module.build_run_artifact(
        _summary(selected),
        golden=golden_200,
        selected_candidates=selected,
        provider="openrouter",
        model="test-model",
        run_at=NOW,
        shard_index=2,
        shard_count=4,
    )

    assert run["prompt_version"] == "email-intent-v1"
    assert run["shard"] == {"index": 2, "count": 4, "case_count": 50}
    assert run["cases"][0]["case_id"] == "email_case_051"
    assert "ground_truth" not in run["cases"][0]
    assert "gmail_content" not in json.dumps(run)


def test_select_shard_is_contiguous_and_limited_to_fifty_cases() -> None:
    module = load_module()
    candidate_cases = candidates(200)["cases"]
    assert isinstance(candidate_cases, list)

    selected = module.select_shard(candidate_cases, shard_index=2, shard_count=4, limit=50)

    assert [case["case_id"] for case in selected] == [
        f"email_case_{index:03d}" for index in range(51, 101)
    ]


def test_build_run_artifact_rejects_selected_candidate_and_golden_id_mismatch() -> None:
    module = load_module()
    candidate_dataset = candidates(1)
    selected = candidate_dataset["cases"]
    assert isinstance(selected, list)
    mismatched_golden = golden(1)
    golden_cases = mismatched_golden["cases"]
    assert isinstance(golden_cases, list)
    golden_cases[0]["case_id"] = "email_case_other"

    with pytest.raises(ValueError, match="case_id"):
        module.build_run_artifact(
            _summary(selected),
            golden=mismatched_golden,
            selected_candidates=selected,
            provider="gemini",
            model="test-model",
            run_at=NOW,
            shard_index=1,
            shard_count=1,
        )


def test_cli_writes_only_a_metadata_safe_run_and_never_writes_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    candidate_path = tmp_path / "candidates.json"
    golden_path = tmp_path / "golden.json"
    runs_dir = tmp_path / "runs"
    candidate_path.write_text(json.dumps(candidates(1)), encoding="utf-8")
    golden_value = golden(1)
    golden_path.write_text(json.dumps(golden_value), encoding="utf-8")

    monkeypatch.setattr(
        module, "build_live_classifier", lambda: (FakeClassifier(), "gemini", "test")
    )
    original_write_text = Path.write_text

    def write_text(path: Path, *args: object, **kwargs: object) -> int:
        if path == golden_path:
            raise AssertionError("the evaluator must never write the golden artifact")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)

    result = run_cli(
        "evaluate_email_golden",
        "--candidates",
        str(candidate_path),
        "--golden",
        str(golden_path),
        "--runs-dir",
        str(runs_dir),
        "--shard-index",
        "1",
        "--shard-count",
        "1",
        "--limit",
        "1",
    )

    run_paths = list(runs_dir.glob("*.json"))
    assert result.returncode == 0
    assert golden_path.read_text(encoding="utf-8") == json.dumps(golden_value)
    assert len(run_paths) == 1
    assert "gmail_content" not in run_paths[0].read_text(encoding="utf-8")
    assert "gmail_content" not in result.stdout
    assert not list(tmp_path.glob("*.md"))


def test_cli_rejects_invalid_selected_identity_before_constructing_or_calling_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    candidate_path = tmp_path / "candidates.json"
    golden_path = tmp_path / "golden.json"
    runs_dir = tmp_path / "runs"
    candidate_value = candidates(1)
    golden_value = golden(1)
    golden_cases = golden_value["cases"]
    assert isinstance(golden_cases, list)
    golden_cases[0]["source_message_id"] = "different-synthetic-message"
    candidate_path.write_text(json.dumps(candidate_value), encoding="utf-8")
    golden_path.write_text(json.dumps(golden_value), encoding="utf-8")

    constructed = False
    classified = False

    class UnexpectedClassifier:
        async def classify(self, user_timezone, current_time, messages):
            nonlocal classified
            del user_timezone, current_time, messages
            classified = True
            raise AssertionError("invalid selected identities must not be classified")

    def build_unexpected_classifier():
        nonlocal constructed
        constructed = True
        return UnexpectedClassifier(), "gemini", "test"

    monkeypatch.setattr(module, "build_live_classifier", build_unexpected_classifier)

    result = run_cli(
        "evaluate_email_golden",
        "--candidates",
        str(candidate_path),
        "--golden",
        str(golden_path),
        "--runs-dir",
        str(runs_dir),
        "--shard-index",
        "1",
        "--shard-count",
        "1",
        "--limit",
        "1",
    )

    assert result.returncode == 2
    assert "source_message_id does not match" in result.stderr
    assert not constructed
    assert not classified
    assert not runs_dir.exists()


def test_build_envelopes_loads_candidate_content_only_into_ephemeral_messages() -> None:
    module = load_module()
    candidate_cases = candidates(1)["cases"]
    assert isinstance(candidate_cases, list)

    envelopes = module.load_envelopes_from_candidates(candidate_cases)

    assert envelopes[0].gmail_message_id == "synthetic-message-001"
    assert envelopes[0].body_format is BodyFormat.TEXT
    assert envelopes[0].fetch_status is FetchStatus.COMPLETE
