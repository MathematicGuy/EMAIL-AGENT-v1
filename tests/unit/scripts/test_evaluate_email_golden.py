"""Tests for the metadata-only live email-router evaluation harness."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EmailRouteDecision,
    FetchStatus,
    ReasonCode,
    Route,
)
from cowork_agent.features.email_action_plan.schemas import ClassificationResult, ClassifiedMessage
from tests.unit.scripts.cli_harness import load_script


def load_module():
    return load_script("evaluate_email_golden")


def _decision(actionability: Actionability, route: Route) -> EmailRouteDecision:
    return EmailRouteDecision(
        actionability=actionability,
        route=route,
        candidate_action_item=None,
        email_is_sufficient=route is not Route.RETRIEVE_RAG,
        knowledge_gaps=("gap",) if route is Route.RETRIEVE_RAG else (),
        retrieval_query=None,
        expected_document_types=(),
        reason_codes=(ReasonCode.NO_ACTION,) if route is Route.NO_ACTION else (),
        confidence=0.9,
    )


class FakeClassifier:
    async def classify(self, user_timezone, current_time, messages):
        return ClassificationResult(
            tuple(
                ClassifiedMessage(
                    message.gmail_message_id,
                    _decision(
                        (
                            Actionability.INFORMATIONAL
                            if message.gmail_message_id == "m1"
                            else Actionability.ACTION_REQUIRED
                        ),
                        Route.NO_ACTION if message.gmail_message_id == "m1" else Route.RETRIEVE_RAG,
                    ),
                )
                for message in messages
            ),
            batch_count=2,
            filtered_summary="Automated updates were filtered.",
        )


class FakeFallbackClassifier:
    async def classify(self, user_timezone, current_time, messages):
        return ClassificationResult(
            (
                ClassifiedMessage(
                    messages[0].gmail_message_id,
                    _decision(Actionability.INFORMATIONAL, Route.NO_ACTION),
                    is_fallback=True,
                ),
            ),
            batch_count=1,
        )


def test_build_envelopes_uses_candidate_snippets_without_evaluation_text_leaks(
    tmp_path: Path,
) -> None:
    module = load_module()
    dataset = tmp_path / "candidates.json"
    dataset.write_text(
        '[{"id":"email_candidate_001","gmail_message_id":"m1",'
        '"gmail_thread_id":"t1","sender":"Sender <sender@example.com>",'
        '"subject":"Subject","received_at":"2026-08-18T00:00:00+00:00",'
        '"labels":["INBOX"],"snippet":"Body excerpt"}]',
        encoding="utf-8",
    )

    envelopes = module.load_envelopes(dataset)

    assert len(envelopes) == 1
    assert envelopes[0].gmail_message_id == "m1"
    assert envelopes[0].normalized_body == "Body excerpt"
    assert envelopes[0].body_format is BodyFormat.TEXT
    assert envelopes[0].fetch_status is FetchStatus.COMPLETE


def test_evaluation_limit_is_bounded_to_fifty_cases() -> None:
    module = load_module()

    assert module.MAX_CASES == 50


def test_evaluate_and_render_report_separates_predictions_from_ground_truth() -> None:
    module = load_module()
    messages = module.load_envelopes_from_records(
        [
            {
                "gmail_message_id": "m1",
                "gmail_thread_id": "t1",
                "sender": "a@example.com",
                "subject": "one",
                "received_at": "2026-08-18T00:00:00+00:00",
                "labels": [],
                "snippet": "first",
            },
            {
                "gmail_message_id": "m2",
                "gmail_thread_id": "t2",
                "sender": "b@example.com",
                "subject": "two",
                "received_at": "2026-08-18T00:00:00+00:00",
                "labels": [],
                "snippet": "second",
            },
        ]
    )

    summary = asyncio.run(module.evaluate(messages, FakeClassifier(), datetime.now(UTC)))
    report = module.render_report(
        summary, dataset_name="gmail_candidates.json", run_date="2026-08-18"
    )

    assert summary["case_count"] == 2
    assert summary["route_counts"] == {
        "NO_ACTION": 1,
        "DIRECT_PLAN": 0,
        "RETRIEVE_RAG": 1,
    }
    assert "Current production Email Intent Router" in report
    assert "Reviewed route accuracy: **not available**" in report
    assert "--limit 2" in report
    assert "first" not in report
    assert "second" not in report


def test_evaluate_persists_one_prediction_record_per_message() -> None:
    module = load_module()
    messages = module.load_envelopes_from_records(
        [
            {
                "gmail_message_id": "m1",
                "gmail_thread_id": "t1",
                "sender": "a@example.com",
                "subject": "one",
                "received_at": "2026-08-18T00:00:00+00:00",
                "labels": [],
                "snippet": "first",
            }
        ]
    )

    summary = asyncio.run(module.evaluate(messages, FakeClassifier(), datetime.now(UTC)))

    assert summary["results"] == [
        {
            "gmail_message_id": "m1",
            "prediction": {
                "actionability": "informational",
                "candidate_action_item": None,
                "email_is_sufficient": True,
                "knowledge_gaps": [],
                "retrieval_query": None,
                "expected_document_types": [],
                "confidence": 0.9,
                "resolved_route": "no_action",
                "reason_codes": ["no_action"],
                "source_status": "model_prediction",
            },
        }
    ]


def test_evaluate_tracks_explicit_fallback_provenance() -> None:
    module = load_module()
    messages = module.load_envelopes_from_records(
        [
            {
                "gmail_message_id": "m1",
                "gmail_thread_id": "t1",
                "sender": "a@example.com",
                "subject": "one",
                "received_at": "2026-08-18T00:00:00+00:00",
                "labels": [],
                "snippet": "first",
            }
        ]
    )

    summary = asyncio.run(module.evaluate(messages, FakeFallbackClassifier(), datetime.now(UTC)))

    assert summary["missing_ids"] == ["m1"]
    assert summary["fallback_count"] == 1
    assert summary["fallback_ids"] == ["m1"]
    assert summary["results"][0]["prediction"]["source_status"] == "classifier_fallback"


def test_merge_preserves_reviewed_labels_and_removes_obsolete_proposals() -> None:
    module = load_module()
    existing = [
        {
            "id": "email_case_001",
            "gmail_message_id": "m1",
            "sender": "old@example.com",
            "subject": "old",
            "ground_truth": {
                "actionability": "informational",
                "expected_route": "no_action",
                "rationale": "Reviewed label.",
            },
            "ground_truth_proposal": {"expected_route": "retrieve_rag"},
            "proposal_comparison": {"status": "computed"},
        }
    ]
    candidates = [
        {
            "id": "email_candidate_001",
            "gmail_message_id": "m1",
            "sender": "new@example.com",
            "subject": "new",
            "received_at": "2026-08-18T00:00:00+00:00",
            "labels": [],
            "snippet": "first",
        },
        {
            "id": "email_candidate_002",
            "gmail_message_id": "m2",
            "sender": "b@example.com",
            "subject": "two",
            "received_at": "2026-08-18T00:00:00+00:00",
            "labels": [],
            "snippet": "second",
        },
    ]
    current = {
        "results": [
            {
                "gmail_message_id": "m1",
                "prediction": {"actionability": "informational", "resolved_route": "no_action"},
            },
            {
                "gmail_message_id": "m2",
                "prediction": {"actionability": "action_required", "resolved_route": "direct_plan"},
            },
        ]
    }
    merged = module.merge_golden_dataset(
        existing,
        candidates,
        current,
        provider="gemini",
        model="test-model",
        run_at="2026-08-18T00:00:00+00:00",
    )

    assert [item["gmail_message_id"] for item in merged] == ["m1", "m2"]
    assert merged[0]["ground_truth"]["rationale"] == "Reviewed label."
    assert merged[0]["ground_truth_status"] != "proposed"
    assert "ground_truth_proposal" not in merged[0]
    assert "proposal_comparison" not in merged[0]
    assert merged[0]["eval_result"] == {
        "route_match": True,
        "actionability_match": True,
        "status": "computed",
    }
    assert merged[1]["ground_truth"] is None
    assert merged[1]["ground_truth_status"] == "unreviewed"
    assert merged[1]["latest_evaluation"] == {
        "provider": "gemini",
        "model": "test-model",
        "run_at": "2026-08-18T00:00:00+00:00",
        "prompt_version": "current",
    }


def test_merge_does_not_relabel_records_outside_the_current_run() -> None:
    module = load_module()
    existing = [
        {
            "id": "email_case_old",
            "gmail_message_id": "old-message",
            "ground_truth": None,
            "latest_evaluation": {
                "provider": "previous-provider",
                "model": "previous-model",
                "run_at": "2026-08-17T00:00:00+00:00",
            },
        }
    ]
    candidates = [
        {
            "id": "email_candidate_new",
            "gmail_message_id": "new-message",
            "sender": "new@example.com",
            "subject": "new",
            "received_at": "2026-08-18T00:00:00+00:00",
            "labels": [],
            "snippet": "new message",
        }
    ]

    merged = module.merge_golden_dataset(
        existing,
        candidates,
        {
            "results": [
                {
                    "gmail_message_id": "new-message",
                    "prediction": {"actionability": "informational", "resolved_route": "no_action"},
                }
            ]
        },
        provider="openrouter",
        model="test-model",
        run_at="2026-08-18T00:00:00+00:00",
    )

    assert merged[0]["latest_evaluation"]["provider"] == "previous-provider"
    assert merged[1]["latest_evaluation"]["provider"] == "openrouter"
    assert "eval_result" not in merged[0]
    assert "snippet" not in merged[1]


def test_build_live_classifier_supports_openrouter(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "test-model")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")

    classifier, provider, model = module.build_live_classifier()

    assert classifier.__class__.__name__ == "OpenRouterRouteClassifier"
    assert provider == "openrouter"
    assert model == "test-model"
