"""SQLite Task repository: idempotent key, round-trip, run links (T4.1/T4.2).

The body-free privacy proof lives in the workflow integration suite, where
raw bodies actually exist in in-run state.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.domain import ActionFreshness, Priority
from cowork_agent.domain.target_contracts import (
    TASK_PIPELINE_VERSION,
    Actionability,
    PlanStep,
    Route,
    SupportingDocument,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.ports import PersistedTask, TaskPointer
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository

NOW = datetime(2026, 8, 7, 9, tzinfo=UTC)

POINTER = TaskPointer(
    mailbox_connection_id="mbx1",
    provider_thread_id="t1",
    sender_name="Nguyễn An",
    sender_address="an@example.com",
    email_subject="Gửi báo cáo",
    email_received_at=NOW,
)


def task_for(message_id: str, *, run_id: str = "run-1", title: str = "Gửi báo cáo") -> Task:
    return Task(
        task_id=f"task_{message_id}_{run_id}",
        run_id=run_id,
        gmail_message_id=message_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        source_message_ids=(message_id,),
        incident_key=None,
        title=title,
        request_summary="Yêu cầu cần được xử lý.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        priority=Priority.HIGH,
        deadline=NOW,
        action_plan=(PlanStep(1, "Kiểm tra yêu cầu", ("cit_1",)),),
        supporting_documents=(
            SupportingDocument(
                citation_id="cit_1",
                document_id="doc_1",
                title="Sổ tay quy trình",
                section=None,
                url="https://docs.example.com/doc_1",
                relevance_score=0.9,
            ),
        ),
        missing_information=("Thiếu hạn nộp cụ thể",),
        classifier_confidence=0.9,
        generation_confidence=0.8,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=NOW,
    )


def record_for(
    message_id: str, *, run_id: str = "run-1", title: str = "Gửi báo cáo"
) -> PersistedTask:
    return PersistedTask(
        task=task_for(message_id, run_id=run_id, title=title),
        pointer=POINTER,
        fingerprint=f"fp_{message_id}",
    )


def test_save_and_list_round_trip_preserves_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteTaskRepository(tmp_path / "tasks.db")
        await repository.initialize()
        original = record_for("m1")

        await repository.save_task(
            original,
            tenant_id="local",
            user_id="u1",
            pipeline_version=TASK_PIPELINE_VERSION,
            run_id="run-1",
        )
        stored = await repository.list_for_run("run-1")

        assert len(stored) == 1
        assert stored[0].task == original.task
        assert stored[0].pointer == POINTER
        assert stored[0].fingerprint == original.fingerprint
        assert stored[0].freshness is ActionFreshness.NEW
        assert await repository.list_for_run("run-other") == ()

    asyncio.run(scenario())


def test_save_is_idempotent_and_keeps_every_producing_run_linked(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteTaskRepository(tmp_path / "tasks.db")
        await repository.initialize()
        kwargs = {
            "tenant_id": "local",
            "user_id": "u1",
            "pipeline_version": TASK_PIPELINE_VERSION,
        }
        await repository.save_task(record_for("m1", run_id="run-1"), run_id="run-1", **kwargs)

        # Replay from a second run: same idempotent key updates the row,
        # links both runs, and stamps the second run's freshness as seen.
        await repository.save_task(
            record_for("m1", run_id="run-2", title="Bản cập nhật"), run_id="run-2", **kwargs
        )

        first_view = await repository.list_for_run("run-1")
        second_view = await repository.list_for_run("run-2")
        assert len(first_view) == len(second_view) == 1
        assert first_view[0].task.title == "Bản cập nhật"
        assert first_view[0].freshness is ActionFreshness.NEW
        assert second_view[0].freshness is ActionFreshness.SEEN

        # A different pipeline version is a distinct durable row linked only
        # to the run that produced it; the version-1 row keeps its own links.
        await repository.save_task(
            record_for("m1", run_id="run-3"),
            run_id="run-3",
            tenant_id="local",
            user_id="u1",
            pipeline_version="2",
        )
        third_view = await repository.list_for_run("run-3")
        assert len(third_view) == 1
        assert len(await repository.list_for_run("run-1")) == 1

    asyncio.run(scenario())
