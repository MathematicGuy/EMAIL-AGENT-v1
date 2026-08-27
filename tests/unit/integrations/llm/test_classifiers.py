"""Route Classifier adapters: bounded batching, §12.2 repair retry, fallback."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from cowork_agent.config import (
    GeminiSettings,
    MimoSettings,
    MistralSettings,
    OpenRouterSettings,
)
from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EphemeralEmailEnvelope,
    ExpectedDocumentType,
    FetchStatus,
    ReasonCode,
    Route,
)
from cowork_agent.integrations.llm.providers.gemini import (
    CLASSIFICATION_SCHEMA,
    CLASSIFIER_REPAIR_INSTRUCTION,
    CLASSIFIER_SYSTEM_INSTRUCTION,
    FALLBACK_ROUTE_DECISION,
    FILTERED_SUMMARY_SCHEMA,
    FILTERED_SUMMARY_SYSTEM_INSTRUCTION,
    GeminiRateLimitError,
    GeminiRouteClassifier,
)
from cowork_agent.integrations.llm.providers.mimo import MimoRouteClassifier
from cowork_agent.integrations.llm.providers.openrouter import OpenRouterRouteClassifier


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "GEMINI_API_KEY_1": "key-one",
        "GEMINI_API_KEY_2": "key-two",
        "GEMINI_API_KEY_3": "key-three",
        "GEMINI_MODEL": "test-model",
        "GEMINI_KEY_ROTATION_STRATEGY": "round_robin",
        "GEMINI_ROTATE_ON_RATE_LIMIT": "true",
        "GEMINI_MAX_ATTEMPTS_PER_REQUEST": "3",
    }
    values.update(overrides)
    return values


def envelope(message_id: str) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="run-1",
        user_id="user-1",
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        gmail_url=f"https://mail.example.com/{message_id}",
        sender_name="Sender",
        sender_email="sender@example.com",
        recipients=("user@example.com",),
        subject=f"Subject {message_id}",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        labels=("INBOX",),
        normalized_body=f"body-{message_id}",
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


def decision_payload(message_id: str, **overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "providerMessageId": message_id,
        "actionability": "action_required",
        "candidateActionItem": f"Handle {message_id}",
        "emailIsSufficient": True,
        "knowledgeGaps": [],
        "retrievalQuery": None,
        "expectedDocumentTypes": [],
        "reasonCodes": ["email_self_contained"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


class ClassifierRecordingTransport:
    """Replays queued payloads (or raises queued exceptions) and records calls."""

    def __init__(self, outcomes: Sequence[Mapping[str, Any] | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.keys: list[str] = []
        self.prompts: list[str] = []
        self.schemas: list[Mapping[str, object]] = []
        self.system_instructions: list[str | None] = []

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        schema: Mapping[str, object],
        timeout_seconds: int,
        system_instruction: str | None = None,
    ) -> Mapping[str, Any]:
        del model, timeout_seconds
        self.keys.append(api_key)
        self.prompts.append(prompt)
        self.schemas.append(schema)
        self.system_instructions.append(system_instruction)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gemini_classifier(transport: ClassifierRecordingTransport) -> GeminiRouteClassifier:
    settings = GeminiSettings.from_env(environment())
    return GeminiRouteClassifier(settings, transport)


def test_classifier_batches_five_five_two_and_binds_one_decision_per_message() -> None:
    ids = [f"msg-{index:02d}" for index in range(1, 13)]
    batches = [ids[0:5], ids[5:10], ids[10:12]]
    outcomes = [{"emails": [decision_payload(mid) for mid in batch]} for batch in batches]

    async def scenario() -> None:
        transport = ClassifierRecordingTransport(outcomes)
        result = await gemini_classifier(transport).classify(
            "Asia/Ho_Chi_Minh", datetime.now(UTC), tuple(map(envelope, ids))
        )

        assert result.batch_count == 3
        assert len(transport.prompts) == 3
        assert [classified.gmail_message_id for classified in result.decisions] == ids
        for classified in result.decisions:
            assert classified.decision.actionability is Actionability.ACTION_REQUIRED
            assert classified.decision.candidate_action_item == (
                f"Handle {classified.gmail_message_id}"
            )

    asyncio.run(scenario())


def test_classifier_parses_all_route_decision_enums() -> None:
    payload = {
        "emails": [
            decision_payload(
                "msg-enums",
                actionability="action_suggested",
                candidateActionItem="Review the contract",
                emailIsSufficient=False,
                knowledgeGaps=["expense policy", "approval threshold"],
                retrievalQuery="expense approval policy",
                expectedDocumentTypes=[
                    "company_policy",
                    "governance_document",
                    "procedure",
                    "guideline",
                    "template",
                    "product_documentation",
                ],
                reasonCodes=[
                    "no_action",
                    "email_self_contained",
                    "company_procedure_required",
                    "governance_required",
                    "policy_required",
                    "template_required",
                    "internal_term_unresolved",
                    "domain_knowledge_required",
                ],
                confidence=0.42,
            )
        ]
    }

    async def scenario() -> None:
        transport = ClassifierRecordingTransport([payload])
        result = await gemini_classifier(transport).classify(
            "UTC", datetime.now(UTC), (envelope("msg-enums"),)
        )

        assert result.batch_count == 1
        decision = result.decisions[0].decision
        assert decision.actionability is Actionability.ACTION_SUGGESTED
        assert decision.candidate_action_item == "Review the contract"
        assert decision.email_is_sufficient is False
        assert decision.knowledge_gaps == ("expense policy", "approval threshold")
        assert decision.retrieval_query == "expense approval policy"
        assert decision.expected_document_types == tuple(ExpectedDocumentType)
        assert decision.reason_codes == tuple(ReasonCode)
        assert decision.confidence == 0.42

    asyncio.run(scenario())


def test_invalid_enum_triggers_exactly_one_repair_retry() -> None:
    broken = {
        "emails": [
            decision_payload("msg-1"),
            decision_payload("msg-2", actionability="not_an_enum"),
        ]
    }
    repaired = {
        "emails": [
            decision_payload("msg-1"),
            decision_payload(
                "msg-2",
                actionability="informational",
                candidateActionItem=None,
                reasonCodes=["no_action"],
            ),
        ]
    }

    async def scenario() -> None:
        transport = ClassifierRecordingTransport(
            [
                broken,
                repaired,
                {"filteredSummary": "Các email còn lại là bản tin cập nhật."},
            ]
        )
        result = await gemini_classifier(transport).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"), envelope("msg-2"))
        )

        assert len(transport.prompts) == 3
        assert CLASSIFIER_REPAIR_INSTRUCTION not in transport.prompts[0]
        assert transport.prompts[1].endswith(CLASSIFIER_REPAIR_INSTRUCTION)
        assert transport.schemas == [
            CLASSIFICATION_SCHEMA,
            CLASSIFICATION_SCHEMA,
            FILTERED_SUMMARY_SCHEMA,
        ]
        assert transport.system_instructions == [
            CLASSIFIER_SYSTEM_INSTRUCTION,
            CLASSIFIER_SYSTEM_INSTRUCTION,
            FILTERED_SUMMARY_SYSTEM_INSTRUCTION,
        ]
        assert result.decisions[0].decision.candidate_action_item == "Handle msg-1"
        assert result.decisions[1].decision.actionability is Actionability.INFORMATIONAL
        assert result.decisions[1].decision is not FALLBACK_ROUTE_DECISION
        assert result.filtered_summary == "Các email còn lại là bản tin cập nhật."

    asyncio.run(scenario())


def test_both_attempts_invalid_fall_back_only_for_affected_messages() -> None:
    # confidence 2.5 is out of range, so msg-2 stays invalid on both attempts.
    broken = {"emails": [decision_payload("msg-1"), decision_payload("msg-2", confidence=2.5)]}

    async def scenario() -> None:
        transport = ClassifierRecordingTransport([broken, broken])
        result = await gemini_classifier(transport).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"), envelope("msg-2"))
        )

        assert len(transport.prompts) == 2
        assert result.decisions[0].decision.actionability is Actionability.ACTION_REQUIRED
        fallback = result.decisions[1].decision
        assert fallback == FALLBACK_ROUTE_DECISION
        assert fallback.actionability is Actionability.UNCLEAR
        assert fallback.route is Route.RETRIEVE_RAG
        assert fallback.confidence == 0.0
        assert fallback.email_is_sufficient is False
        assert fallback.candidate_action_item is None
        assert fallback.retrieval_query is None
        assert fallback.expected_document_types == ()
        assert fallback.knowledge_gaps == ("classifier output unavailable",)
        assert fallback.reason_codes == (ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED,)

    asyncio.run(scenario())


def test_missing_decision_is_repaired_on_the_retry() -> None:
    incomplete = {"emails": [decision_payload("msg-1")]}
    complete = {"emails": [decision_payload("msg-1"), decision_payload("msg-2")]}

    async def scenario() -> None:
        transport = ClassifierRecordingTransport([incomplete, complete])
        result = await gemini_classifier(transport).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"), envelope("msg-2"))
        )

        assert len(transport.prompts) == 2
        assert [classified.gmail_message_id for classified in result.decisions] == [
            "msg-1",
            "msg-2",
        ]
        assert result.decisions[1].decision is not FALLBACK_ROUTE_DECISION
        assert result.decisions[1].decision.candidate_action_item == "Handle msg-2"

    asyncio.run(scenario())


def test_missing_decision_on_both_attempts_falls_back_without_raising() -> None:
    incomplete = {"emails": [decision_payload("msg-1")]}

    async def scenario() -> None:
        transport = ClassifierRecordingTransport([incomplete, incomplete])
        result = await gemini_classifier(transport).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"), envelope("msg-2"))
        )

        assert len(transport.prompts) == 2
        assert result.decisions[0].decision is not FALLBACK_ROUTE_DECISION
        assert result.decisions[1].decision == FALLBACK_ROUTE_DECISION

    asyncio.run(scenario())


def test_transport_outage_falls_back_for_every_message_without_raising() -> None:
    async def scenario() -> None:
        transport = ClassifierRecordingTransport([RuntimeError("timeout"), RuntimeError("timeout")])
        result = await gemini_classifier(transport).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"), envelope("msg-2"))
        )

        assert result.batch_count == 1
        assert len(transport.prompts) == 2
        fallback_decisions = [classified.decision for classified in result.decisions]
        assert all(decision == FALLBACK_ROUTE_DECISION for decision in fallback_decisions)

    asyncio.run(scenario())


def test_classifier_rotates_key_on_rate_limit_without_counting_a_retry() -> None:
    class RateLimitOnceTransport(ClassifierRecordingTransport):
        def __init__(self, outcomes: Sequence[Mapping[str, Any] | Exception]) -> None:
            super().__init__(outcomes)
            self._rate_limited = False

        async def generate(
            self,
            *,
            api_key: str,
            model: str,
            prompt: str,
            schema: Mapping[str, object],
            timeout_seconds: int,
            system_instruction: str | None = None,
        ) -> Mapping[str, Any]:
            if api_key == "key-one" and not self._rate_limited:
                self._rate_limited = True
                self.keys.append(api_key)
                raise GeminiRateLimitError("quota")
            return await super().generate(
                api_key=api_key,
                model=model,
                prompt=prompt,
                schema=schema,
                timeout_seconds=timeout_seconds,
                system_instruction=system_instruction,
            )

    async def scenario() -> None:
        transport = RateLimitOnceTransport([{"emails": [decision_payload("msg-1")]}])
        result = await gemini_classifier(transport).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"),)
        )

        assert transport.keys == ["key-one", "key-two"]
        assert len(transport.prompts) == 1
        assert result.decisions[0].decision is not FALLBACK_ROUTE_DECISION

    asyncio.run(scenario())


def test_mimo_classifier_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []
    payload = {
        "emails": [
            decision_payload("msg-1"),
            decision_payload(
                "msg-2",
                actionability="informational",
                candidateActionItem=None,
                reasonCodes=["no_action"],
            ),
        ]
    }

    def fake_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, timeout_seconds
        captured.append(body)
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    monkeypatch.setattr("cowork_agent.integrations.llm.providers.mimo._post_json", fake_post_json)

    async def scenario() -> None:
        settings = MimoSettings.from_env({"MIMO_API_KEY": "test-key"})
        result = await MimoRouteClassifier(settings).classify(
            "Asia/Ho_Chi_Minh", datetime.now(UTC), (envelope("msg-1"), envelope("msg-2"))
        )

        assert result.batch_count == 1
        assert [classified.gmail_message_id for classified in result.decisions] == [
            "msg-1",
            "msg-2",
        ]
        assert result.decisions[0].decision.actionability is Actionability.ACTION_REQUIRED
        assert result.decisions[1].decision.actionability is Actionability.INFORMATIONAL

    asyncio.run(scenario())
    assert len(captured) == 1


def test_mistral_malformed_transport_json_falls_back_without_logging_email_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from cowork_agent.integrations.llm.providers.mistral import MistralRouteClassifier

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"not-json"

    def fake_urlopen(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        return FakeResponse()

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.openai_transport.urlopen", fake_urlopen
    )
    settings = MistralSettings.from_env(
        {"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "test-model"},
    )

    async def scenario() -> None:
        result = await MistralRouteClassifier(settings).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"),)
        )
        assert result.decisions[0].decision == FALLBACK_ROUTE_DECISION

    asyncio.run(scenario())
    assert "body-msg-1" not in caplog.text


def test_mistral_classifier_parses_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cowork_agent.integrations.llm.providers.mistral import MistralRouteClassifier

    captured: list[dict[str, object]] = []

    def fake_post_json(
        url: str, api_key: str, body: dict[str, object], timeout_seconds: int
    ) -> dict[str, object]:
        del url, api_key, timeout_seconds
        captured.append(body)
        return {
            "choices": [
                {"message": {"content": json.dumps({"emails": [decision_payload("msg-1")]})}}
            ]
        }

    monkeypatch.setattr(
        "cowork_agent.integrations.llm.providers.mistral._post_json", fake_post_json
    )
    settings = MistralSettings.from_env(
        {"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "test-model"},
    )

    async def scenario() -> None:
        result = await MistralRouteClassifier(settings).classify(
            "UTC", datetime.now(UTC), (envelope("msg-1"),)
        )
        assert result.decisions[0].decision.candidate_action_item == "Handle msg-1"

    asyncio.run(scenario())
    assert len(captured) == 1


def test_all_email_classifier_telemetry_uses_the_immutable_prompt_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cowork_agent.integrations.llm.providers import base, gemini, mimo, mistral, openrouter
    from cowork_agent.integrations.llm.providers.mistral import MistralRouteClassifier

    observed_versions: list[object] = []

    def record(**kwargs: object) -> None:
        for value in kwargs.values():
            if isinstance(value, Mapping) and "prompt_version" in value:
                observed_versions.append(value["prompt_version"])

    monkeypatch.setattr(base, "_update_current_span", record)
    for provider in (gemini, mimo, mistral, openrouter):
        monkeypatch.setattr(provider, "_update_current_generation", record)

    def response(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "choices": [{"message": {"content": json.dumps({"emails": [decision_payload("msg")]})}}]
        }

    monkeypatch.setattr(mimo, "_post_json", response)
    monkeypatch.setattr(mistral, "_post_json", response)
    monkeypatch.setattr(openrouter, "_post_json", response)

    async def scenario() -> None:
        message = (envelope("msg"),)
        await gemini_classifier(
            ClassifierRecordingTransport([{"emails": [decision_payload("msg")]}])
        ).classify("UTC", datetime.now(UTC), message)
        await MimoRouteClassifier(MimoSettings.from_env({"MIMO_API_KEY": "test-key"})).classify(
            "UTC", datetime.now(UTC), message
        )
        await MistralRouteClassifier(
            MistralSettings.from_env({"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "test-model"})
        ).classify("UTC", datetime.now(UTC), message)
        await OpenRouterRouteClassifier(
            OpenRouterSettings.from_env(
                {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "test-model"},
            )
        ).classify("UTC", datetime.now(UTC), message)

    asyncio.run(scenario())

    assert observed_versions
    assert set(observed_versions) == {"email-intent-v1"}
