from datetime import UTC, datetime, timedelta

from cowork_agent.domain import ActionPlanStep, Confidence, DeadlineSource, EvidenceRef
from cowork_agent.domain.target_contracts import (
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
)
from cowork_agent.features.email_action_plan.schemas import EmailExtraction, ExtractedAction
from cowork_agent.features.email_action_plan.shaping import (
    batch_messages,
    group_by_thread,
    merge_correlated_emails,
)


def test_batch_messages_splits_threads_into_small_batches() -> None:
    now = datetime.now(UTC)
    threads = tuple(
        EphemeralEmailEnvelope(
            run_id="",
            tenant_id="",
            user_id="",
            gmail_message_id=f"message-{index}",
            gmail_thread_id=f"thread-{index}",
            gmail_url="",
            sender_name="",
            sender_email="sender@example.com",
            recipients=(),
            subject=f"Subject {index}",
            received_at=now - timedelta(minutes=index),
            labels=(),
            normalized_body="Please review this item.",
            body_format=BodyFormat.TEXT,
            attachments_present=False,
            fetch_status=FetchStatus.COMPLETE,
        )
        for index in range(12)
    )

    batches = batch_messages(group_by_thread(threads), 5)

    assert [sum(len(thread) for thread in batch) for batch in batches] == [5, 5, 2]


def test_merge_correlated_emails_preserves_incident_correlation_and_impact() -> None:
    build_steps = (
        ActionPlanStep(1, "Mở build logs để tìm lỗi đầu tiên.", "email"),
        ActionPlanStep(2, "Sửa lỗi và chạy lại bản build để xác minh.", "suggestion"),
    )
    evidence = (EvidenceRef("email_body", None, None, "Build failed!", "build-message"),)
    build_action = ExtractedAction(
        provider_message_id="build-message",
        title="Xử lý build production HR-Chatbot",
        summary="Build production thất bại.",
        deadline_at=None,
        deadline_text=None,
        deadline_source=DeadlineSource.NONE,
        action_plan=build_steps,
        evidence=evidence,
        confidence=Confidence.HIGH,
        required=True,
        explicit_blocker=True,
        impact="production_blocked",
        incident_key="railway:eloquent-victory:hr-chatbot:production",
        related_message_ids=("build-message", "volume-message"),
    )
    volume_action = ExtractedAction(
        provider_message_id="volume-message",
        title="Hủy lịch xóa volume HR-Chatbot",
        summary="Volume đang chờ xóa tự động.",
        deadline_at=None,
        deadline_text=None,
        deadline_source=DeadlineSource.NONE,
        action_plan=build_steps,
        evidence=evidence,
        confidence=Confidence.HIGH,
        required=True,
        explicit_blocker=True,
        impact="data_loss_risk",
        incident_key="railway:eloquent-victory:hr-chatbot:hr-chatbot-volume",
        related_message_ids=("volume-message",),
    )

    merged_emails = merge_correlated_emails(
        (
            EmailExtraction(
                "build-message",
                "actionable",
                "Build production bị chặn.",
                (build_action,),
            ),
            EmailExtraction(
                "volume-message",
                "actionable",
                "Volume có nguy cơ bị xóa.",
                (volume_action,),
            ),
        )
    )
    merged_actions = [item for email_result in merged_emails for item in email_result.action_items]
    assert len(merged_actions) == 1
    assert merged_actions[0].impact == "data_loss_risk"
    assert merged_actions[0].related_message_ids == ("build-message", "volume-message")
    assert "HR-Chatbot" in merged_actions[0].title
    assert len(merged_actions[0].action_plan) <= 5
