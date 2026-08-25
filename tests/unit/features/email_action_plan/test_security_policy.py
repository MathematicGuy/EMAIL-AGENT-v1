"""Unit tests for email security evaluation policy and quarantine task creation (Task 1.4)."""

from datetime import UTC, datetime

from cowork_agent.domain.models import Priority
from cowork_agent.domain.target_contracts import (
    Actionability,
    BodyFormat,
    EmailSourceLink,
    EphemeralEmailEnvelope,
    FetchStatus,
    LinkSafetyReport,
    Route,
    SecurityScanResult,
    ThreatCategory,
    ThreatLevel,
)
from cowork_agent.features.email_action_plan.security_policy import (
    create_quarantined_task,
    evaluate_email_security,
)


def _make_envelope(
    message_id: str = "msg-malicious-001",
    subject: str = "URGENT: Verify your account immediately",
) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="run-1",
        user_id="user-1",
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        sender_name="Security Team",
        sender_email="attacker@spoofed-bank.com",
        recipients=(),
        subject=subject,
        received_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
        labels=(),
        normalized_body="Click here to reset: https://pаypal.com/signin",
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
        source_links=(
            EmailSourceLink(
                ref="link1",
                label="Sign In",
                url="https://pаypal.com/signin",
                threat_level=ThreatLevel.MALICIOUS,
            ),
        ),
    )


def test_evaluate_email_security_levels():
    # Clean
    clean_res = SecurityScanResult(
        email_id="msg-1",
        overall_threat_level=ThreatLevel.CLEAN,
        scanned_at=datetime.now(UTC),
        links=(),
        attachments=(),
        quarantined=False,
        recommended_action="allow",
    )
    quarantined, level, action = evaluate_email_security(clean_res)
    assert quarantined is False
    assert level == ThreatLevel.CLEAN
    assert action == "allow"

    # Suspicious (warning, not quarantined)
    suspicious_res = SecurityScanResult(
        email_id="msg-2",
        overall_threat_level=ThreatLevel.SUSPICIOUS,
        scanned_at=datetime.now(UTC),
        links=(),
        attachments=(),
        quarantined=False,
        recommended_action="warn",
    )
    quarantined, level, action = evaluate_email_security(suspicious_res)
    assert quarantined is False
    assert level == ThreatLevel.SUSPICIOUS
    assert action == "warn"

    # Malicious (quarantined)
    malicious_res = SecurityScanResult(
        email_id="msg-3",
        overall_threat_level=ThreatLevel.MALICIOUS,
        scanned_at=datetime.now(UTC),
        links=(),
        attachments=(),
        quarantined=True,
        recommended_action="quarantine",
    )
    quarantined, level, action = evaluate_email_security(malicious_res)
    assert quarantined is True
    assert level == ThreatLevel.MALICIOUS
    assert action == "quarantine"

    # Blocked (quarantined)
    blocked_res = SecurityScanResult(
        email_id="msg-4",
        overall_threat_level=ThreatLevel.BLOCKED,
        scanned_at=datetime.now(UTC),
        links=(),
        attachments=(),
        quarantined=True,
        recommended_action="quarantine",
    )
    quarantined, level, action = evaluate_email_security(blocked_res)
    assert quarantined is True
    assert level == ThreatLevel.BLOCKED
    assert action == "quarantine"


def test_create_quarantined_task():
    envelope = _make_envelope()
    scan_result = SecurityScanResult(
        email_id=envelope.gmail_message_id,
        overall_threat_level=ThreatLevel.MALICIOUS,
        scanned_at=datetime.now(UTC),
        links=(
            LinkSafetyReport(
                original_url="https://pаypal.com/signin",
                resolved_url="https://pаypal.com/signin",
                threat_level=ThreatLevel.MALICIOUS,
                threat_category=ThreatCategory.HOMOGRAPH_SPOOF,
                details="Homograph attack spoofing paypal",
            ),
        ),
        attachments=(),
        quarantined=True,
        recommended_action="quarantine",
    )
    clock = datetime(2026, 8, 25, 10, 5, tzinfo=UTC)
    persisted = create_quarantined_task(
        envelope=envelope,
        scan_result=scan_result,
        run_id="run-sec-001",
        mailbox_connection_id="mbx-001",
        clock=clock,
    )

    task = persisted.task
    assert task.quarantined is True
    assert task.security_threat_level == ThreatLevel.MALICIOUS
    assert task.priority == Priority.URGENT
    assert task.actionability == Actionability.ACTION_REQUIRED
    assert task.route == Route.NO_ACTION
    assert "[CẢNH BÁO BẢO MẬT]" in task.title
    assert "pаypal.com" in task.request_summary or "Homograph attack" in task.request_summary
    assert len(task.action_plan) == 2
    assert "CẢNH BÁO" in task.action_plan[0].instruction
    assert persisted.pointer.sender_address == "attacker@spoofed-bank.com"
    assert persisted.pointer.email_subject == envelope.subject
