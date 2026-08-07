"""Frozen HTTP contract: run creation, status, and result shapes."""

import asyncio
from datetime import UTC, datetime

from conftest import make_email, make_task

EMPTY_STATE_MESSAGE = "Không có công việc cần xử lý"


def _tasks_for(*message_ids: str) -> tuple:
    return tuple(make_task(message_id, f"Việc {message_id}") for message_id in message_ids)


def test_create_run_returns_202_with_pollable_shape(compat_session) -> None:
    async def scenario() -> None:
        async with compat_session([make_email("m1", "t1", "Gửi báo cáo")], _tasks_for("m1")) as s:
            response = await s.post_run("contract-create")
            assert response.status_code == 202
            payload = response.json()
            assert set(payload) == {"id", "status", "statusUrl"}
            assert payload["status"] == "queued"
            assert payload["statusUrl"].startswith(f"/v1/mail-todo/runs/{payload['id']}")

    asyncio.run(scenario())


def test_duplicate_idempotency_key_returns_same_run(compat_session) -> None:
    async def scenario() -> None:
        async with compat_session() as s:
            first = await s.post_run("same-key")
            second = await s.post_run("same-key")
            assert first.status_code == second.status_code == 202
            assert first.json()["id"] == second.json()["id"]
            assert len(s.app.state.run_repository.runs) == 1

    asyncio.run(scenario())


def test_get_run_exposes_progress_and_error_shape(compat_session) -> None:
    async def scenario() -> None:
        async with compat_session([make_email("m1", "t1", "Việc")], _tasks_for("m1")) as s:
            created = await s.post_run("status-shape")
            run_id = created.json()["id"]
            response = await s.get_run(run_id)
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "succeeded"
            assert payload["progress"]["emailsMatched"] == 1
            assert payload["progress"]["emailsProcessed"] == 1
            assert payload["error"] is None

    asyncio.run(scenario())


def test_result_before_terminal_state_returns_run_not_complete(compat_session) -> None:
    class GatedMailbox:
        def __init__(self, messages, gate: asyncio.Event) -> None:
            self._inner_messages = messages
            self._gate = gate

        async def search_unread(self, connection_id, query, page_size, cursor=None):
            await self._gate.wait()
            from cowork_agent.features.email_action_plan.schemas import SearchPage

            return SearchPage((), None, 0)

        async def get_thread(self, connection_id, thread_id):
            return ()

    async def scenario() -> None:
        gate = asyncio.Event()
        messages = [make_email("m1", "t1", "Việc")]
        mailbox = GatedMailbox(messages, gate)
        async with compat_session(messages, _tasks_for("m1"), mailbox=mailbox) as s:
            post_task = asyncio.create_task(s.post_run("blocked-run"))
            run_id = None
            for _ in range(200):
                await asyncio.sleep(0.01)
                if s.app.state.run_repository.runs:
                    run_id = next(iter(s.app.state.run_repository.runs))
                    break
            assert run_id is not None
            response = await s.get_result(run_id)
            assert response.status_code == 409
            assert response.json()["detail"] == "RUN_NOT_COMPLETE"
            gate.set()
            assert (await post_task).status_code == 202

    asyncio.run(scenario())


def test_result_shape_keys_are_exactly_the_frozen_set(compat_session) -> None:
    async def scenario() -> None:
        async with compat_session([make_email("m1", "t1", "Việc")], _tasks_for("m1")) as s:
            created = await s.post_run("result-keys")
            payload = (await s.get_result(created.json()["id"])).json()
            assert set(payload) == {
                "run",
                "actionItems",
                "nextActions",
                "attachmentWarnings",
                "processedEmails",
                "message",
            }
            assert payload["message"] is None
            assert payload["attachmentWarnings"] == []

    asyncio.run(scenario())


def test_next_actions_are_first_three_action_items(compat_session) -> None:
    async def scenario() -> None:
        messages = [make_email(f"m{index}", f"t{index}", f"Chủ đề {index}") for index in range(5)]
        tasks = tuple(
            make_task(
                f"m{index}",
                f"Việc {index}",
                deadline=datetime(2026, 8, 10 + index, tzinfo=UTC),
            )
            for index in range(5)
        )
        async with compat_session(messages, tasks) as s:
            created = await s.post_run("next-actions")
            payload = (await s.get_result(created.json()["id"])).json()
            assert len(payload["actionItems"]) == 5
            assert payload["nextActions"] == payload["actionItems"][:3]

    asyncio.run(scenario())


def test_empty_run_returns_explicit_empty_state_message(compat_session) -> None:
    async def scenario() -> None:
        async with compat_session() as s:
            created = await s.post_run("empty-state")
            payload = (await s.get_result(created.json()["id"])).json()
            assert payload["message"] == EMPTY_STATE_MESSAGE
            assert payload["actionItems"] == []
            assert payload["nextActions"] == []

    asyncio.run(scenario())


def test_tasks_endpoint_exposes_persisted_task_contracts(compat_session) -> None:
    async def scenario() -> None:
        async with compat_session([make_email("m1", "t1", "Việc")], _tasks_for("m1")) as s:
            created = await s.post_run("tasks-endpoint")
            run_id = created.json()["id"]
            response = await s.client.get(f"/v1/mail-todo/runs/{run_id}/tasks")
            assert response.status_code == 200
            payload = response.json()
            assert set(payload) == {"tasks"}
            assert len(payload["tasks"]) == 1
            task = payload["tasks"][0]
            assert task["gmail_message_id"] == "m1"
            assert task["title"] == "Việc m1"
            assert task["validation_status"] == "system_generated"
            assert "action_plan" in task and "missing_information" in task

    asyncio.run(scenario())


def test_processed_emails_are_development_only(compat_session, monkeypatch) -> None:
    async def scenario() -> None:
        async with compat_session([make_email("m1", "t1", "Việc")], _tasks_for("m1")) as s:
            created = await s.post_run("dev-metadata")
            run_id = created.json()["id"]

            dev_result = (await s.get_result(run_id)).json()
            assert dev_result["processedEmails"] and "processedEmails" in dev_result
            assert "processedEmails" in (await s.get_run(run_id)).json()

            monkeypatch.setenv("APP_ENV", "production")
            prod_result = (await s.get_result(run_id)).json()
            assert "processedEmails" not in prod_result
            assert "processedEmails" not in (await s.get_run(run_id)).json()

    asyncio.run(scenario())
