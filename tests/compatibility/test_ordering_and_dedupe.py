"""Frozen behavior: priority ordering, in-run dedupe, cross-run freshness."""

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import make_email

from cowork_agent.domain import ActionPlanStep, Confidence, DeadlineSource, EvidenceRef
from cowork_agent.features.email_action_plan.schemas import (
    EmailExtraction,
    ExtractedAction,
    ExtractionBatch,
)


def _action(
    message_id: str,
    title: str,
    *,
    deadline_at: datetime | None = None,
    required: bool = True,
    explicit_blocker: bool = False,
    impact: str = "none",
    incident_key: str | None = None,
) -> ExtractedAction:
    return ExtractedAction(
        provider_message_id=message_id,
        title=title,
        summary="Yêu cầu cần xử lý.",
        deadline_at=deadline_at,
        deadline_text=deadline_at.isoformat() if deadline_at else None,
        deadline_source=DeadlineSource.EXPLICIT if deadline_at else DeadlineSource.NONE,
        action_plan=(ActionPlanStep(1, title, "email"),),
        evidence=(EvidenceRef("email_body", None, None, "Trích dẫn ngắn."),),
        confidence=Confidence.HIGH,
        required=required,
        explicit_blocker=explicit_blocker,
        impact=impact,
        incident_key=incident_key,
    )


def test_ordering_is_priority_then_deadline_presence_then_deadline(compat_session) -> None:
    async def scenario() -> None:
        base = datetime.now(UTC)
        batch = ExtractionBatch(
            (
                EmailExtraction(
                    "m-plain", "actionable", "Có yêu cầu", (_action("m-plain", "Việc thường"),)
                ),
                EmailExtraction(
                    "m-far",
                    "actionable",
                    "Có yêu cầu",
                    (_action("m-far", "Việc hạn muộn", deadline_at=base + timedelta(days=10)),),
                ),
                EmailExtraction(
                    "m-near",
                    "actionable",
                    "Có yêu cầu",
                    (_action("m-near", "Việc hạn gần", deadline_at=base + timedelta(days=5)),),
                ),
                EmailExtraction(
                    "m-blocker",
                    "actionable",
                    "Đang chặn tiến độ",
                    (_action("m-blocker", "Việc chặn", explicit_blocker=True),),
                ),
                EmailExtraction(
                    "m-outage",
                    "actionable",
                    "Nguy cơ mất dữ liệu",
                    (_action("m-outage", "Việc khẩn", impact="data_loss_risk"),),
                ),
            )
        )
        messages = [
            make_email("m-plain", "t-plain", "Việc thường"),
            make_email("m-far", "t-far", "Việc hạn muộn"),
            make_email("m-near", "t-near", "Việc hạn gần"),
            make_email("m-blocker", "t-blocker", "Việc chặn"),
            make_email("m-outage", "t-outage", "Việc khẩn"),
        ]
        async with compat_session(messages, batch) as s:
            created = await s.post_run("ordering")
            payload = (await s.get_result(created.json()["id"])).json()
            assert [item["title"] for item in payload["actionItems"]] == [
                "Việc khẩn",
                "Việc chặn",
                "Việc hạn gần",
                "Việc hạn muộn",
                "Việc thường",
            ]
            assert [item["priority"] for item in payload["actionItems"]] == [
                "urgent",
                "high",
                "medium",
                "medium",
                "medium",
            ]

    asyncio.run(scenario())


def test_duplicate_fingerprint_dedupes_within_one_run(compat_session) -> None:
    async def scenario() -> None:
        batch = ExtractionBatch(
            (
                EmailExtraction(
                    "m-a",
                    "actionable",
                    "Có yêu cầu",
                    (_action("m-a", "Xử lý sự cố", incident_key="INC-7"),),
                ),
                EmailExtraction(
                    "m-b",
                    "actionable",
                    "Có yêu cầu",
                    (_action("m-b", "Xử lý sự cố", incident_key="INC-7"),),
                ),
            )
        )
        messages = [
            make_email("m-a", "t-a", "Báo cáo sự cố"),
            make_email("m-b", "t-b", "Nhắc sự cố"),
        ]
        async with compat_session(messages, batch) as s:
            created = await s.post_run("dedupe")
            payload = (await s.get_result(created.json()["id"])).json()
            assert len(payload["actionItems"]) == 1
            assert payload["actionItems"][0]["title"] == "Xử lý sự cố"

    asyncio.run(scenario())


def test_fingerprint_seen_marks_second_run_items_as_seen(compat_session) -> None:
    async def scenario() -> None:
        def batch() -> ExtractionBatch:
            return ExtractionBatch(
                (
                    EmailExtraction(
                        "m1", "actionable", "Có yêu cầu", (_action("m1", "Việc lặp lại"),)
                    ),
                )
            )

        messages = [make_email("m1", "t1", "Yêu cầu lặp lại")]
        async with compat_session(messages, batch()) as s:
            first = await s.post_run("freshness-first")
            first_items = (await s.get_result(first.json()["id"])).json()["actionItems"]
            assert first_items[0]["freshness"] == "new"

            second = await s.post_run("freshness-second")
            second_items = (await s.get_result(second.json()["id"])).json()["actionItems"]
            assert second_items[0]["freshness"] == "seen"
            assert first.json()["id"] != second.json()["id"]

    asyncio.run(scenario())
