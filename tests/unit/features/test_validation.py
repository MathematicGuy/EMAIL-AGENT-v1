"""Output validator tests (PRD-v1 FR-10, master-comparison §6.6)."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from cowork_agent.domain.target_contracts import (
    Actionability,
    ActionPlanOutput,
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
    PlanStep,
    RetrievalStatus,
    Route,
    SemanticChunk,
    SemanticRetrievalResponse,
    SupportingDocument,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.routing import RouteResolution
from cowork_agent.features.email_action_plan.validation import (
    ACTIONABILITY_NOT_ALLOWED,
    CITATION_NOT_IN_RETRIEVAL,
    DIRECT_PLAN_WITH_CITATIONS,
    EMPTY_ACTION_PLAN,
    EMPTY_STEP_INSTRUCTION,
    MISSING_REQUEST_SUMMARY,
    MISSING_TITLE,
    RAW_BODY_LEAK,
    SEVERITY_FATAL,
    SEVERITY_REPAIRABLE,
    ValidationResult,
    contains_body_fragment,
    validate_action_plan,
)

_NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)
#: Collapsed length 64 — inside the whole-body containment band (40..160).
_MEDIUM_BODY = " ".join(f"từ{i}" for i in range(15))
#: Collapsed length 289 — above the band, matched via 160-char windows.
_LONG_BODY = " ".join(f"từ{i}" for i in range(60))


def _step(
    instruction: str = "Kiểm tra yêu cầu",
    *,
    citations: tuple[str, ...] = (),
    number: int = 1,
) -> PlanStep:
    return PlanStep(step=number, instruction=instruction, supporting_citation_ids=citations)


def _document(citation_id: str) -> SupportingDocument:
    return SupportingDocument(
        citation_id=citation_id,
        document_id=f"doc_{citation_id}",
        title="Sổ tay quy trình nội bộ",
        section="Chương 1",
        url=f"https://docs.example.com/{citation_id}",
        relevance_score=0.9,
    )


def _task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "task_id": "task_1",
        "run_id": "run_1",
        "gmail_message_id": "m1",
        "gmail_url": "https://mail.google.com/mail/u/0/#inbox/m1",
        "source_message_ids": ("m1",),
        "incident_key": None,
        "title": "Gửi báo cáo tuần",
        "request_summary": "Gửi báo cáo tuần trước thứ Sáu.",
        "actionability": Actionability.ACTION_REQUIRED,
        "route": Route.DIRECT_PLAN,
        "priority": None,
        "deadline": None,
        "action_plan": (_step(),),
        "supporting_documents": (),
        "missing_information": (),
        "classifier_confidence": 0.9,
        "generation_confidence": 0.9,
        "validation_status": ValidationStatus.SYSTEM_GENERATED,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def _envelope(body: str) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="run_1",
        user_id="user_1",
        gmail_message_id="m1",
        gmail_thread_id="t1",
        gmail_url="https://mail.google.com/mail/u/0/#inbox/m1",
        sender_name="Nguyễn An",
        sender_email="an@example.com",
        recipients=(),
        subject="Yêu cầu công việc",
        received_at=_NOW,
        labels=(),
        normalized_body=body,
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


def _resolution(route: Route = Route.DIRECT_PLAN) -> RouteResolution:
    return RouteResolution(route=route, reason_codes=(), forced_by_guard=False, mode="full")


def _retrieval(chunk_ids: Sequence[str] = ()) -> SemanticRetrievalResponse:
    return SemanticRetrievalResponse(
        query_id="query_1",
        chunks=tuple(
            SemanticChunk(
                chunk_id=chunk_id,
                document_id=f"doc_{chunk_id}",
                document_title="Sổ tay quy trình nội bộ",
                section=None,
                text="Nội dung tri thức công ty.",
                source_url="https://docs.example.com",
                document_version=None,
                relevance_score=0.9,
                rerank_score=None,
            )
            for chunk_id in chunk_ids
        ),
        retrieval_status=RetrievalStatus.SUCCESS,
        latency_ms=12,
    )


def _validate(
    task: Task,
    *,
    resolution: RouteResolution | None = None,
    retrieval: SemanticRetrievalResponse | None = None,
    envelopes: Sequence[EphemeralEmailEnvelope] = (),
) -> ValidationResult:
    return validate_action_plan(
        ActionPlanOutput(task=task),
        resolution=resolution or _resolution(task.route),
        retrieval=retrieval,
        envelopes=envelopes,
    )


def test_valid_task_passes_untouched() -> None:
    task = _task()
    result = _validate(task)
    assert result.task == task
    assert result.violations == ()


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"title": "   "}, MISSING_TITLE),
        ({"request_summary": ""}, MISSING_REQUEST_SUMMARY),
        ({"action_plan": ()}, EMPTY_ACTION_PLAN),
        ({"action_plan": (_step("Bước một"), _step(" "))}, EMPTY_STEP_INSTRUCTION),
        ({"actionability": Actionability.UNCLEAR}, ACTIONABILITY_NOT_ALLOWED),
        ({"actionability": Actionability.IRRELEVANT}, ACTIONABILITY_NOT_ALLOWED),
    ],
)
def test_fatal_violation_drops_task(overrides: dict[str, object], expected_code: str) -> None:
    result = _validate(_task(**overrides))
    assert result.task is None
    assert [violation.code for violation in result.violations] == [expected_code]
    assert {violation.severity for violation in result.violations} == {SEVERITY_FATAL}


def test_fatal_violation_reports_no_repairable_violations() -> None:
    task = _task(title="", supporting_documents=(_document("cit_1"),))
    result = _validate(task, retrieval=_retrieval(("cit_1",)))
    assert result.task is None
    assert [violation.code for violation in result.violations] == [MISSING_TITLE]


def test_short_body_echo_is_not_a_leak() -> None:
    body = "Nội dung ngắn."
    result = _validate(_task(request_summary=body), envelopes=[_envelope(body)])
    assert result.task is not None
    assert result.violations == ()


@pytest.mark.parametrize("field", ["title", "request_summary"])
def test_medium_body_echo_drops_task(field: str) -> None:
    result = _validate(_task(**{field: _MEDIUM_BODY}), envelopes=[_envelope(_MEDIUM_BODY)])
    assert result.task is None
    assert [violation.code for violation in result.violations] == [RAW_BODY_LEAK]


def test_leak_in_step_instruction_drops_task() -> None:
    task = _task(action_plan=(_step(_MEDIUM_BODY),))
    result = _validate(task, envelopes=[_envelope(_MEDIUM_BODY)])
    assert result.task is None
    assert [violation.code for violation in result.violations] == [RAW_BODY_LEAK]


def test_medium_body_partial_echo_passes() -> None:
    fragment = " ".join(_MEDIUM_BODY.split()[:4])
    result = _validate(_task(request_summary=fragment), envelopes=[_envelope(_MEDIUM_BODY)])
    assert result.task is not None
    assert result.violations == ()


def test_long_body_window_leak_drops_task() -> None:
    collapsed = " ".join(_LONG_BODY.split())
    window = collapsed[40:200]
    result = _validate(
        _task(request_summary=f"Trước {window} sau"), envelopes=[_envelope(_LONG_BODY)]
    )
    assert result.task is None
    assert [violation.code for violation in result.violations] == [RAW_BODY_LEAK]


def test_long_body_near_miss_under_window_passes() -> None:
    fragment = " ".join(_LONG_BODY.split())[:159]
    result = _validate(_task(request_summary=fragment), envelopes=[_envelope(_LONG_BODY)])
    assert result.task is not None
    assert result.violations == ()


def test_contains_body_fragment_thresholds() -> None:
    short = "Quá ngắn."
    assert contains_body_fragment(short, short) is False
    assert contains_body_fragment(_MEDIUM_BODY, _MEDIUM_BODY) is True
    assert contains_body_fragment(_MEDIUM_BODY.upper(), _MEDIUM_BODY) is True
    assert contains_body_fragment(_MEDIUM_BODY.replace(" ", "   "), _MEDIUM_BODY) is True
    half = " ".join(_MEDIUM_BODY.split()[:7])
    assert contains_body_fragment(half, _MEDIUM_BODY) is False
    collapsed = " ".join(_LONG_BODY.split())
    assert contains_body_fragment(f"mở đầu {collapsed[40:200]} kết thúc", _LONG_BODY) is True
    assert contains_body_fragment(collapsed[:159], _LONG_BODY) is False


def test_steps_are_renumbered_without_violation() -> None:
    task = _task(action_plan=(_step("Bước một", number=4), _step("Bước hai", number=9)))
    result = _validate(task)
    assert result.violations == ()
    assert result.task is not None
    assert [step.step for step in result.task.action_plan] == [1, 2]
    assert [step.instruction for step in result.task.action_plan] == ["Bước một", "Bước hai"]


def test_direct_plan_strips_all_citations_even_supported() -> None:
    task = _task(
        supporting_documents=(_document("cit_1"),),
        action_plan=(_step("Bước một", citations=("cit_1",)), _step("Bước hai")),
    )
    result = _validate(task, retrieval=_retrieval(("cit_1",)))
    assert result.task is not None
    assert result.task.supporting_documents == ()
    assert [step.supporting_citation_ids for step in result.task.action_plan] == [(), ()]
    assert result.task.missing_information == ()
    assert [violation.code for violation in result.violations] == [DIRECT_PLAN_WITH_CITATIONS]
    assert {violation.severity for violation in result.violations} == {SEVERITY_REPAIRABLE}


def test_only_unsupported_citations_are_stripped() -> None:
    task = _task(
        route=Route.RETRIEVE_RAG,
        supporting_documents=(_document("cit_ok"), _document("cit_bad")),
        action_plan=(
            _step("Bước một", citations=("cit_ok", "cit_bad")),
            _step("Bước hai", citations=("cit_ok",)),
        ),
    )
    result = _validate(
        task,
        resolution=_resolution(Route.RETRIEVE_RAG),
        retrieval=_retrieval(("cit_ok",)),
    )
    assert result.task is not None
    assert [document.citation_id for document in result.task.supporting_documents] == ["cit_ok"]
    assert [step.supporting_citation_ids for step in result.task.action_plan] == [
        ("cit_ok",),
        ("cit_ok",),
    ]
    assert len(result.task.missing_information) == 1
    assert "cit_bad" in result.task.missing_information[0]
    assert [violation.code for violation in result.violations] == [CITATION_NOT_IN_RETRIEVAL]


def test_missing_information_deduped_per_stripped_citation() -> None:
    task = _task(
        route=Route.RETRIEVE_RAG,
        supporting_documents=(_document("cit_bad"), _document("cit_bad")),
        action_plan=(_step("Bước một", citations=("cit_bad",)),),
    )
    result = _validate(task, resolution=_resolution(Route.RETRIEVE_RAG), retrieval=None)
    assert result.task is not None
    notes = [entry for entry in result.task.missing_information if "cit_bad" in entry]
    assert len(notes) == 1


def test_empty_retrieval_strips_every_citation() -> None:
    task = _task(
        route=Route.RETRIEVE_RAG,
        supporting_documents=(_document("cit_1"),),
        action_plan=(_step("Bước một", citations=("cit_1",)),),
    )
    for retrieval in (None, _retrieval()):
        result = _validate(
            task, resolution=_resolution(Route.RETRIEVE_RAG), retrieval=retrieval
        )
        assert result.task is not None
        assert result.task.supporting_documents == ()
        assert result.task.action_plan[0].supporting_citation_ids == ()
        assert len(result.task.missing_information) == 1
        assert [violation.code for violation in result.violations] == [CITATION_NOT_IN_RETRIEVAL]


def test_supported_citations_pass_without_violation() -> None:
    task = _task(
        route=Route.RETRIEVE_RAG,
        supporting_documents=(_document("cit_1"),),
        action_plan=(_step("Bước một", citations=("cit_1",)),),
    )
    result = _validate(
        task, resolution=_resolution(Route.RETRIEVE_RAG), retrieval=_retrieval(("cit_1",))
    )
    assert result.violations == ()
    assert result.task == task
