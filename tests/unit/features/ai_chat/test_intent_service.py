import asyncio
from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
    ReadyDocumentRef,
)
from cowork_agent.features.ai_chat.intent.observability import RecordingIntentRoutingSink
from cowork_agent.features.ai_chat.intent.service import (
    ChatRoutingService,
    IntentClassifierInvalidOutput,
)


class Catalog:
    def __init__(self, documents: tuple[ReadyDocumentRef, ...]) -> None:
        self.documents = documents

    async def list_ready(self, scope: object, *, at: object) -> tuple[ReadyDocumentRef, ...]:
        del scope, at
        return self.documents


class Classifier:
    def __init__(self, results: list[IntentDecision | Exception]) -> None:
        self.results = results
        self.calls = 0

    async def classify(self, classifier_input: object) -> IntentDecision:
        del classifier_input
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


def _scope() -> ChatMemoryScope:
    return ChatMemoryScope("tenant-1", "user-1", "session-1")


def _request() -> ChatMessageRequest:
    return ChatMessageRequest("session-1", "What were the requirements?", "idem-1")


def _rag_decision() -> IntentDecision:
    return IntentDecision(
        ChatIntent.KNOWLEDGE_QUERY,
        True,
        False,
        None,
        False,
        "requirements",
        0.9,
        (IntentReasonCode.USER_DOCUMENT_REQUIRED,),
    )


def _service(classifier: Classifier, documents: tuple[ReadyDocumentRef, ...]):
    sink = RecordingIntentRoutingSink()
    service = ChatRoutingService(
        classifier=classifier,
        catalog=Catalog(documents),
        model_id="model",
        sink=sink,
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    return service, sink


def test_success_produces_one_decision_without_retry() -> None:
    classifier = Classifier([_rag_decision()])
    service, sink = _service(classifier, (ReadyDocumentRef("doc-1", "Guide"),))

    outcome = asyncio.run(service.route(scope=_scope(), request=_request(), recent_turns=()))

    assert classifier.calls == 1
    assert outcome.route is ChatRoute.RAG
    assert outcome.classifier_retried is False
    assert [event.name for event in sink.events][-1] == "chat.route.decided"


def test_schema_failure_retries_once_then_fails_open_with_ready_documents() -> None:
    classifier = Classifier(
        [IntentClassifierInvalidOutput("bad"), IntentClassifierInvalidOutput("bad")]
    )
    service, _ = _service(classifier, (ReadyDocumentRef("doc-1", "Guide"),))

    outcome = asyncio.run(service.route(scope=_scope(), request=_request(), recent_turns=()))

    assert classifier.calls == 2
    assert outcome.route is ChatRoute.RAG
    assert outcome.fallback_used is True
    assert outcome.classifier_retried is True
    assert IntentReasonCode.CLASSIFIER_UNAVAILABLE in outcome.reason_codes


def test_failure_without_ready_documents_falls_back_to_chat() -> None:
    classifier = Classifier(
        [IntentClassifierInvalidOutput("bad"), IntentClassifierInvalidOutput("bad")]
    )
    service, _ = _service(classifier, ())

    outcome = asyncio.run(service.route(scope=_scope(), request=_request(), recent_turns=()))

    assert outcome.route is ChatRoute.CHAT
    assert outcome.retrieval_query is None
    assert IntentReasonCode.NO_READY_DOCUMENTS in outcome.reason_codes
