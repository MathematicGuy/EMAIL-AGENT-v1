"""Output validators for generated Tasks (PRD-v1 FR-10, master-comparison §6.6).

Pure, synchronous validation of one Generator output. Fatal violations
(missing required fields, disallowed Actionability, raw-body leaks) drop the
Task; repairable violations (Route/Citation inconsistencies) sanitize it.
No I/O, no framework imports; repairs build new frozen dataclasses via
``dataclasses.replace`` and never mutate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from cowork_agent.domain.target_contracts import (
    Actionability,
    ActionPlanOutput,
    EphemeralEmailEnvelope,
    Route,
    SemanticRetrievalResponse,
    Task,
)

from .routing import RouteResolution

SEVERITY_REPAIRABLE = "repairable"
SEVERITY_FATAL = "fatal"

MISSING_TITLE = "MISSING_TITLE"
MISSING_REQUEST_SUMMARY = "MISSING_REQUEST_SUMMARY"
EMPTY_ACTION_PLAN = "EMPTY_ACTION_PLAN"
EMPTY_STEP_INSTRUCTION = "EMPTY_STEP_INSTRUCTION"
ACTIONABILITY_NOT_ALLOWED = "ACTIONABILITY_NOT_ALLOWED"
RAW_BODY_LEAK = "RAW_BODY_LEAK"
DIRECT_PLAN_WITH_CITATIONS = "DIRECT_PLAN_WITH_CITATIONS"
CITATION_NOT_IN_RETRIEVAL = "CITATION_NOT_IN_RETRIEVAL"

#: Actionability values allowed to produce a persisted Task (§6.6).
_PLAN_ACTIONABILITY = frozenset(
    {
        Actionability.ACTION_REQUIRED,
        Actionability.ACTION_SUGGESTED,
        Actionability.INFORMATIONAL,
    }
)

#: Privacy thresholds in characters after whitespace collapse: bodies below
#: the minimum never count as a leak; up to the window size the whole body
#: must reappear; above it, any one window-sized slice suffices.
_LEAK_MINIMUM = 40
_LEAK_WINDOW = 160
_LEAK_WINDOW_STEP = 40


@dataclass(frozen=True, slots=True)
class ValidationViolation:
    """One FR-10 violation found in a generated Task.

    ``detail`` is safe text only — it never quotes email content.
    """

    code: str
    detail: str
    severity: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validator verdict: the sanitized Task, or None when a violation is fatal."""

    task: Task | None
    violations: tuple[ValidationViolation, ...]


def _violation(code: str, detail: str, severity: str) -> ValidationViolation:
    return ValidationViolation(code=code, detail=detail, severity=severity)


def _collapse(text: str) -> str:
    return " ".join(text.split()).casefold()


def contains_body_fragment(field: str, body: str) -> bool:
    """Return True when ``field`` reproduces a substantial fragment of ``body``.

    Both strings are compared case-insensitively with whitespace collapsed.
    Bodies shorter than the minimum never count; bodies up to the window size
    must reappear whole; longer bodies match when any window-sized contiguous
    slice (stepping by 40 characters) reappears.
    """
    return _fragment_in(_collapse(field), _collapse(body))


def _fragment_in(collapsed_field: str, collapsed_body: str) -> bool:
    if len(collapsed_body) < _LEAK_MINIMUM:
        return False
    if len(collapsed_body) <= _LEAK_WINDOW:
        return collapsed_body in collapsed_field
    return any(
        collapsed_body[offset : offset + _LEAK_WINDOW] in collapsed_field
        for offset in range(0, len(collapsed_body) - _LEAK_WINDOW + 1, _LEAK_WINDOW_STEP)
    )


def _privacy_sensitive_fields(task: Task) -> tuple[str, ...]:
    return (
        task.title,
        task.request_summary,
        *task.missing_information,
        *(step.instruction for step in task.action_plan),
        *(
            text
            for document in task.supporting_documents
            for text in (document.title, document.section)
            if text is not None
        ),
    )


def _renumber_steps(task: Task) -> Task:
    renumbered = tuple(
        replace(step, step=number) for number, step in enumerate(task.action_plan, start=1)
    )
    if renumbered == task.action_plan:
        return task
    return replace(task, action_plan=renumbered)


def _sanitize_citations(
    task: Task,
    resolution: RouteResolution,
    retrieval: SemanticRetrievalResponse | None,
) -> tuple[Task, tuple[ValidationViolation, ...]]:
    cited = bool(task.supporting_documents) or any(
        step.supporting_citation_ids for step in task.action_plan
    )
    if not cited:
        return task, ()
    # The Route Resolver's route is authoritative; the Task's own route is
    # model-reported output and is not trusted for policy decisions.
    if resolution.route is Route.DIRECT_PLAN:
        # DIRECT_PLAN runs without retrieval, so no Citation can be grounded.
        stripped = replace(
            task,
            supporting_documents=(),
            action_plan=tuple(
                replace(step, supporting_citation_ids=()) for step in task.action_plan
            ),
        )
        detail = (
            "Công việc DIRECT_PLAN không kèm truy xuất tri thức; toàn bộ trích dẫn đã bị loại bỏ."
        )
        return stripped, (_violation(DIRECT_PLAN_WITH_CITATIONS, detail, SEVERITY_REPAIRABLE),)
    known_ids = (
        frozenset(chunk.chunk_id for chunk in retrieval.chunks)
        if retrieval is not None
        else frozenset()
    )
    stripped_ids = tuple(
        dict.fromkeys(
            (
                *(
                    document.citation_id
                    for document in task.supporting_documents
                    if document.citation_id not in known_ids
                ),
                *(
                    citation_id
                    for step in task.action_plan
                    for citation_id in step.supporting_citation_ids
                    if citation_id not in known_ids
                ),
            )
        )
    )
    if not stripped_ids:
        return task, ()
    sanitized = replace(
        task,
        supporting_documents=tuple(
            document for document in task.supporting_documents if document.citation_id in known_ids
        ),
        action_plan=tuple(
            replace(
                step,
                supporting_citation_ids=tuple(
                    citation_id
                    for citation_id in step.supporting_citation_ids
                    if citation_id in known_ids
                ),
            )
            for step in task.action_plan
        ),
        missing_information=task.missing_information
        + tuple(_missing_citation_note(citation_id) for citation_id in stripped_ids),
    )
    detail = (
        f"Các trích dẫn không có trong kết quả truy xuất: {', '.join(stripped_ids)}; "
        "nội dung liên quan đã bị loại bỏ."
    )
    return sanitized, (_violation(CITATION_NOT_IN_RETRIEVAL, detail, SEVERITY_REPAIRABLE),)


def _missing_citation_note(citation_id: str) -> str:
    return f'Tài liệu tham chiếu "{citation_id}" không khả dụng; nội dung liên quan đã bị loại bỏ.'


def validate_action_plan(
    output: ActionPlanOutput,
    *,
    resolution: RouteResolution,
    retrieval: SemanticRetrievalResponse | None,
    envelopes: Sequence[EphemeralEmailEnvelope],
) -> ValidationResult:
    """Validate one Generator output against the §6.6 rules (PRD-v1 FR-10).

    Fatal checks (required fields, allowed Actionability, raw-body privacy
    leaks against the Ephemeral Envelopes) drop the Task; when none fire,
    step numbers are normalized to 1..N and Citations are sanitized against
    the resolved Route (authoritative) and the retrieval result.
    """
    task = output.task
    violations: list[ValidationViolation] = []
    if not task.title.strip():
        violations.append(_violation(MISSING_TITLE, "Tiêu đề công việc bị thiếu.", SEVERITY_FATAL))
    if not task.request_summary.strip():
        violations.append(
            _violation(MISSING_REQUEST_SUMMARY, "Tóm tắt yêu cầu bị thiếu.", SEVERITY_FATAL)
        )
    if not task.action_plan:
        violations.append(
            _violation(EMPTY_ACTION_PLAN, "Kế hoạch hành động không có bước nào.", SEVERITY_FATAL)
        )
    if any(not step.instruction.strip() for step in task.action_plan):
        violations.append(
            _violation(
                EMPTY_STEP_INSTRUCTION,
                "Kế hoạch hành động có bước trống nội dung.",
                SEVERITY_FATAL,
            )
        )
    allows_unclear_partial = (
        task.actionability is Actionability.UNCLEAR and resolution.mode == "partial"
    )
    if task.actionability not in _PLAN_ACTIONABILITY and not allows_unclear_partial:
        violations.append(
            _violation(
                ACTIONABILITY_NOT_ALLOWED,
                "Mức độ hành động không cho phép tạo công việc.",
                SEVERITY_FATAL,
            )
        )
    fields = _privacy_sensitive_fields(task)
    collapsed_bodies = tuple(_collapse(envelope.normalized_body) for envelope in envelopes)
    if any(_fragment_in(_collapse(field), body) for body in collapsed_bodies for field in fields):
        violations.append(
            _violation(
                RAW_BODY_LEAK,
                "Kết quả trùng với nội dung email gốc nên đã bị loại bỏ để bảo mật.",
                SEVERITY_FATAL,
            )
        )
    if violations:
        return ValidationResult(task=None, violations=tuple(violations))
    sanitized = _renumber_steps(task)
    sanitized, repairable = _sanitize_citations(sanitized, resolution, retrieval)
    return ValidationResult(task=sanitized, violations=repairable)
