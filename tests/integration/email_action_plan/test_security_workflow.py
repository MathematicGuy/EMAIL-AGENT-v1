from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain import Priority, RunStatus
from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EmailSourceLink,
    EphemeralEmailEnvelope,
    FetchStatus,
    PlanStep,
    Route,
    Task,
    ThreatCategory,
    ThreatLevel,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.compat_mapper import legacy_result_shape
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import (
    CreateDigestRun,
    DigestWorker,
)
from cowork_agent.integrations.gmail.fakes import FakeMailbox, SafeTextAttachmentExtractor
from cowork_agent.integrations.llm.fakes import FakePlanGenerator, FakeRouteClassifier
from cowork_agent.integrations.security.fakes import FakeEmailSecurityScanner, FakeThreatIntel
from cowork_agent.persistence.repositories.local import (
    InMemoryResultRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)

NOW = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)


def _envelope(
    message_id: str,
    subject: str,
    body: str,
    source_links: tuple[EmailSourceLink, ...] = (),
) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="",
        user_id="",
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        sender_name="Sender",
        sender_email="sender@example.com",
        recipients=(),
        subject=subject,
        received_at=NOW,
        labels=(),
        normalized_body=body,
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
        source_links=source_links,
    )


def _task_for(message_id: str, title: str) -> Task:
    return Task(
        task_id=f"task_{message_id}",
        run_id="run-test",
        gmail_message_id=message_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        source_message_ids=(message_id,),
        incident_key=None,
        title=title,
        request_summary="Yêu cầu cần được xử lý.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        priority=Priority.HIGH,
        deadline=NOW + timedelta(days=1),
        action_plan=(PlanStep(1, "Kiểm tra yêu cầu", ()), PlanStep(2, title, ())),
        supporting_documents=(),
        missing_information=(),
        classifier_confidence=0.9,
        generation_confidence=0.9,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_workflow_quarantines_malicious_email_and_generates_alert_task() -> None:
    # 1. Prepare 2 emails: 1 clean task email, 1 phishing email
    clean_email = _envelope("m1", "Review report Q3", "Please review Q3 report by Friday")
    phishing_link = EmailSourceLink(
        "link1", "Login", "https://g\u043e\u043egle.com/login", ThreatLevel.MALICIOUS
    )
    malicious_email = _envelope(
        "m2",
        "Security Alert: Reset Password",
        "Click here: https://gооgle.com/login",
        source_links=(phishing_link,),
    )

    threat_intel = FakeThreatIntel()
    threat_intel.register_threat_url(
        "https://g\u043e\u043egle.com/login",
        threat_level=ThreatLevel.MALICIOUS,
        threat_category=ThreatCategory.HOMOGRAPH_SPOOF,
        details="Cyrillic homograph spoofing google.com",
    )
    security_scanner = FakeEmailSecurityScanner(threat_intel=threat_intel)

    runs = InMemoryRunRepository()
    results = InMemoryResultRepository()
    task_repo = InMemoryTaskRepository()
    short_term = ShortTermStore()

    creator = CreateDigestRun(runs)
    run = await creator.execute(
        user_id="user-1",
        mailbox_connection_id="mbx-1",
        idempotency_key="run-sec-key-1",
        now=NOW,
    )

    classifier = FakeRouteClassifier()
    generator = FakePlanGenerator((_task_for("m1", "Review report Q3"),))

    worker = DigestWorker(
        runs=runs,
        results=results,
        mailbox=FakeMailbox([clean_email, malicious_email]),
        attachments=SafeTextAttachmentExtractor(),
        classifier=classifier,
        generator=generator,
        short_term=short_term,
        task_repository=task_repo,
        security_scanner=security_scanner,
    )

    completed_run = await worker.execute(run.id, now=NOW)
    assert completed_run is not None
    assert completed_run.status == RunStatus.SUCCEEDED

    # Verify LLM Isolation: Malicious email m2 was NOT passed to LLM classifier
    classified_ids = {env.gmail_message_id for env in classifier.received_envelopes}
    assert "m1" in classified_ids
    assert "m2" not in classified_ids  # Isolated from LLM classifier!

    # Verify Persisted Tasks: Both generated task for m1 and Quarantine Alert task for m2 exist
    persisted_tasks = await task_repo.list_for_run(run.id)
    assert len(persisted_tasks) == 2

    quarantined_tasks = [p for p in persisted_tasks if p.task.quarantined]
    assert len(quarantined_tasks) == 1
    q_task = quarantined_tasks[0].task
    assert q_task.gmail_message_id == "m2"
    assert q_task.priority == Priority.URGENT
    assert q_task.security_threat_level == ThreatLevel.MALICIOUS
    assert "[CẢNH BÁO BẢO MẬT]" in q_task.title

    # Verify Legacy Result Shape:
    legacy_res = legacy_result_shape(
        run=completed_run,
        persisted=persisted_tasks,
        warnings=(),
        processed_emails=(),
        clock=NOW,
    )
    action_items = legacy_res["actionItems"]
    assert any(
        item.provider_message_id == "m2" and item.priority == Priority.URGENT
        for item in action_items
    )
