"""Security evaluation policy and quarantine task generator for email digest workflow."""

import uuid
from datetime import datetime

from cowork_agent.domain.models import ActionFreshness, Priority
from cowork_agent.domain.target_contracts import (
    Actionability,
    EphemeralEmailEnvelope,
    PlanStep,
    Route,
    SecurityScanResult,
    Task,
    ThreatLevel,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.policies import action_fingerprint
from cowork_agent.features.email_action_plan.ports import PersistedTask, TaskPointer


def evaluate_email_security(
    scan_result: SecurityScanResult,
) -> tuple[bool, ThreatLevel, str]:
    """Evaluate scan result and return (quarantined, overall_threat_level, recommended_action)."""
    threat = scan_result.overall_threat_level
    if threat in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED):
        return True, threat, "quarantine"
    if threat == ThreatLevel.SUSPICIOUS:
        return False, threat, "warn"
    return False, ThreatLevel.CLEAN, "allow"


def create_quarantined_task(
    envelope: EphemeralEmailEnvelope,
    scan_result: SecurityScanResult,
    run_id: str,
    mailbox_connection_id: str,
    clock: datetime,
) -> PersistedTask:
    """Generate an urgent quarantine alert Task from a malicious or blocked email envelope."""
    threat_names: list[str] = []
    for link in scan_result.links:
        if link.threat_level in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED):
            threat_names.append(
                f"Link: {link.original_url} ({link.details or link.threat_category.value})"
            )
    for att in scan_result.attachments:
        if att.threat_level in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED):
            threat_names.append(
                f"Tệp: {att.filename} ({att.reason or att.threat_category.value})"
            )

    threat_summary = (
        "; ".join(threat_names) if threat_names else "Phát hiện liên kết/mã độc nguy hiểm."
    )

    task_id = f"task_{uuid.uuid4().hex[:12]}"
    subject_title = envelope.subject or "(Không có tiêu đề)"
    title = f"[CẢNH BÁO BẢO MẬT] Phát hiện Email độc hại: {subject_title}"
    request_summary = (
        f"Email này đã bị cách ly tự động khỏi bộ xử lý AI để đảm bảo an toàn. "
        f"Chi tiết mối đe dọa: {threat_summary}"
    )

    instruction_1 = (
        "CẢNH BÁO: Tuyệt đối không bấm vào các đường liên kết hoặc mở bất kỳ tệp đính kèm nào "
        "từ email này."
    )
    instruction_2 = (
        f"Kiểm tra lại người gửi ({envelope.sender_email}) qua kênh liên lạc nội bộ hoặc "
        f"báo cáo bộ phận IT/An toàn thông tin."
    )

    action_plan = (
        PlanStep(
            step=1,
            instruction=instruction_1,
            supporting_citation_ids=(),
        ),
        PlanStep(
            step=2,
            instruction=instruction_2,
            supporting_citation_ids=(),
        ),
    )

    fingerprint = action_fingerprint(
        mailbox_connection_id,
        envelope.gmail_thread_id,
        title,
        None,
    )

    task = Task(
        task_id=task_id,
        run_id=run_id,
        gmail_message_id=envelope.gmail_message_id,
        gmail_url=envelope.gmail_url,
        source_message_ids=(envelope.gmail_message_id,),
        incident_key=None,
        title=title,
        request_summary=request_summary,
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.NO_ACTION,
        priority=Priority.URGENT,
        deadline=None,
        action_plan=action_plan,
        supporting_documents=(),
        missing_information=(),
        classifier_confidence=1.0,
        generation_confidence=1.0,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=clock,
        source_links=envelope.source_links,
        security_threat_level=scan_result.overall_threat_level,
        quarantined=True,
        security_reports=scan_result.links,
    )

    pointer = TaskPointer(
        mailbox_connection_id=mailbox_connection_id,
        provider_thread_id=envelope.gmail_thread_id,
        sender_name=envelope.sender_name,
        sender_address=envelope.sender_email,
        email_subject=envelope.subject,
        email_received_at=envelope.received_at,
    )

    return PersistedTask(
        task=task,
        pointer=pointer,
        fingerprint=fingerprint,
        freshness=ActionFreshness.NEW,
    )
