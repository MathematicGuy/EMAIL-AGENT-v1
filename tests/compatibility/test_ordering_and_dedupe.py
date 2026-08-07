"""Frozen behavior: priority ordering, in-run dedupe, cross-run freshness."""

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import make_email, make_task

from cowork_agent.domain import Priority


def test_ordering_is_priority_then_deadline_presence_then_deadline(compat_session) -> None:
    async def scenario() -> None:
        base = datetime.now(UTC)
        tasks = (
            make_task("m-plain", "Việc thường"),
            make_task("m-far", "Việc hạn muộn", deadline=base + timedelta(days=10)),
            make_task("m-near", "Việc hạn gần", deadline=base + timedelta(days=5)),
            make_task("m-blocker", "Việc chặn", priority=Priority.HIGH),
            make_task("m-outage", "Việc khẩn", priority=Priority.URGENT),
        )
        messages = [
            make_email("m-plain", "t-plain", "Việc thường"),
            make_email("m-far", "t-far", "Việc hạn muộn"),
            make_email("m-near", "t-near", "Việc hạn gần"),
            make_email("m-blocker", "t-blocker", "Việc chặn"),
            make_email("m-outage", "t-outage", "Việc khẩn"),
        ]
        async with compat_session(messages, tasks) as s:
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
        tasks = (
            make_task("m-a", "Xử lý sự cố", incident_key="INC-7"),
            make_task("m-b", "Xử lý sự cố", incident_key="INC-7"),
        )
        messages = [
            make_email("m-a", "t-a", "Báo cáo sự cố"),
            make_email("m-b", "t-b", "Nhắc sự cố"),
        ]
        async with compat_session(messages, tasks) as s:
            created = await s.post_run("dedupe")
            payload = (await s.get_result(created.json()["id"])).json()
            assert len(payload["actionItems"]) == 1
            assert payload["actionItems"][0]["title"] == "Xử lý sự cố"

    asyncio.run(scenario())


def test_fingerprint_seen_marks_second_run_items_as_seen(compat_session) -> None:
    async def scenario() -> None:
        def tasks() -> tuple:
            return (make_task("m1", "Việc lặp lại"),)

        messages = [make_email("m1", "t1", "Yêu cầu lặp lại")]
        async with compat_session(messages, tasks()) as s:
            first = await s.post_run("freshness-first")
            first_items = (await s.get_result(first.json()["id"])).json()["actionItems"]
            assert first_items[0]["freshness"] == "new"

            second = await s.post_run("freshness-second")
            second_items = (await s.get_result(second.json()["id"])).json()["actionItems"]
            assert second_items[0]["freshness"] == "seen"
            assert first.json()["id"] != second.json()["id"]

    asyncio.run(scenario())
