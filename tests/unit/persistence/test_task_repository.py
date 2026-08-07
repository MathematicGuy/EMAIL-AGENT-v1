"""SQLite Task repository: idempotent key and round-trip (T4.1).

The body-free privacy proof lives in the workflow integration suite, where
raw bodies actually exist in in-run state.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.domain import Priority
from cowork_agent.domain.target_contracts import (
    TASK_PIPELINE_VERSION,
    Actionability,
    PlanStep,
    Route,
    SupportingDocument,
    Task,
    ValidationStatus,
)
from cowork_agent.persistence.repositories.tasks import SQLiteTaskRepository

NOW = datetime(2026, 8, 7, 9, tzinfo=UTC)


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


def test_save_and_list_round_trip_preserves_task_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteTaskRepository(tmp_path / "tasks.db")
        await repository.initialize()
        original = task_for("m1")

        await repository.save_task(
            tenant_id="local", user_id="u1", pipeline_version=TASK_PIPELINE_VERSION, task=original
        )
        stored = await repository.list_for_run("run-1")

        assert stored == (original,)
        assert await repository.list_for_run("run-other") == ()

    asyncio.run(scenario())


def test_save_is_idempotent_on_tenant_user_message_pipeline_key(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteTaskRepository(tmp_path / "tasks.db")
        await repository.initialize()
        kwargs = {
            "tenant_id": "local",
            "user_id": "u1",
            "pipeline_version": TASK_PIPELINE_VERSION,
        }
        await repository.save_task(task=task_for("m1", run_id="run-1"), **kwargs)

        # Replay from a second run: same idempotent key updates, never duplicates.
        await repository.save_task(
            task=task_for("m1", run_id="run-2", title="Bản cập nhật"), **kwargs
        )

        assert len(await repository.list_for_run("run-2")) == 1
        assert await repository.list_for_run("run-1") == ()
        assert (await repository.list_for_run("run-2"))[0].title == "Bản cập nhật"

        # A different pipeline version is a distinct durable row.
        await repository.save_task(
            task=task_for("m1", run_id="run-3"),
            tenant_id="local",
            user_id="u1",
            pipeline_version="2",
        )
        assert len(await repository.list_for_run("run-3")) == 1

    asyncio.run(scenario())
