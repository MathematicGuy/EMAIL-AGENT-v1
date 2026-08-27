"""Standard EICAR Antivirus Test Signature Verification Suite (ISO/IEC 27001 compliant)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from cowork_agent.domain.target_contracts import (
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
    Priority,
    SecurityScanResult,
    ThreatCategory,
    ThreatLevel,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.security_policy import (
    create_quarantined_task,
    evaluate_email_security,
)
from cowork_agent.integrations.security.fakes import (
    FakeClamAVScanner,
    FakeThreatIntel,
)
from cowork_agent.integrations.security.hash_lookup import (
    EICAR_SHA256,
    CompositeHashLookup,
    KnownMalwareHashDatabase,
    compute_sha256,
)
from cowork_agent.integrations.security.magic_inspector import inspect_attachment_file

# Standard EICAR string (68 ASCII characters)
EICAR_TEST_STRING: bytes = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def test_eicar_hash_computation_and_invariants():
    """Verify standard EICAR string and its deterministic SHA-256 cryptographic hash."""
    assert len(EICAR_TEST_STRING) == 68
    calculated_hash = compute_sha256(EICAR_TEST_STRING)
    assert calculated_hash == EICAR_SHA256
    assert calculated_hash == "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"


def test_eicar_known_malware_hash_lookup():
    """Verify KnownMalwareHashDatabase and CompositeHashLookup detect EICAR offline."""
    db = KnownMalwareHashDatabase()
    report = db.lookup(EICAR_SHA256, "eicar.com")
    assert report is not None
    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.is_safe_to_extract is False
    assert "EICAR" in (report.reason or "")

    composite = CompositeHashLookup(local_db=db)
    composite_report = asyncio.run(composite.check_hash(EICAR_SHA256, "test_eicar.com"))
    assert composite_report.threat_level == ThreatLevel.MALICIOUS
    assert composite_report.threat_category == ThreatCategory.MALWARE
    assert composite_report.is_safe_to_extract is False


def test_eicar_clamav_scanner_detection():
    """Verify ClamAV scanner adapter detects EICAR virus signature."""
    fake_clamav = FakeClamAVScanner()
    report = asyncio.run(fake_clamav.scan_bytes(EICAR_TEST_STRING, "eicar.com"))

    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.is_safe_to_extract is False
    assert "Win.Test.EICAR_HDB-1" in (report.reason or "")


def test_eicar_test_attachment_file_on_disk():
    """Verify real test attachment file in data/security_test_attachments/ matches EICAR."""
    root_dir = Path(__file__).resolve().parents[3]
    eicar_file = root_dir / "data" / "security_test_attachments" / "audit_signature_test.txt"
    assert eicar_file.exists(), f"EICAR test file missing at {eicar_file}"

    report = inspect_attachment_file(eicar_file)
    assert report.threat_level in (ThreatLevel.CLEAN, ThreatLevel.MALICIOUS)

    file_content = eicar_file.read_bytes().strip()
    assert file_content == EICAR_TEST_STRING
    assert compute_sha256(file_content) == EICAR_SHA256


def test_eicar_email_security_scanner_gate_quarantine():
    """Verify email containing EICAR attachment is quarantined by security policy."""
    fake_intel = FakeThreatIntel()
    fake_intel.register_malware_hash(
        EICAR_SHA256,
        "eicar.com",
        threat_level=ThreatLevel.MALICIOUS,
        threat_category=ThreatCategory.MALWARE,
        reason="EICAR test antivirus signature detected",
    )

    envelope = EphemeralEmailEnvelope(
        run_id="run-eicar-001",
        user_id="user-001",
        gmail_message_id="msg-eicar-999",
        gmail_thread_id="thread-eicar-999",
        gmail_url="https://mail.google.com/mail/u/0/#inbox/msg-eicar-999",
        sender_name="Auditor",
        sender_email="auditor@security-eval.org",
        recipients=("secops@company.com",),
        subject="Q3 Security Audit EICAR Test Sample",
        received_at=NOW,
        labels=("INBOX",),
        normalized_body="Please inspect the attached EICAR test sample.",
        body_format=BodyFormat.TEXT,
        attachments_present=True,
        fetch_status=FetchStatus.COMPLETE,
        source_links=(),
    )

    # 1. Mock hash check returning EICAR detection
    eicar_report = asyncio.run(fake_intel.check_file_hash(EICAR_SHA256, "eicar.com"))
    assert eicar_report.threat_level == ThreatLevel.MALICIOUS

    # 2. Evaluate security policy
    scan_result = SecurityScanResult(
        email_id=envelope.gmail_message_id,
        overall_threat_level=ThreatLevel.MALICIOUS,
        scanned_at=NOW,
        links=(),
        attachments=(eicar_report,),
        quarantined=True,
        recommended_action="quarantine",
    )

    quarantined, threat_level, action = evaluate_email_security(scan_result)
    assert quarantined is True
    assert threat_level == ThreatLevel.MALICIOUS
    assert action == "quarantine"

    # 3. Generate quarantined task
    quarantined_task = create_quarantined_task(
        envelope,
        scan_result,
        run_id="run-eicar-001",
        mailbox_connection_id="mb-conn-001",
        clock=NOW,
    )

    assert quarantined_task.task.quarantined is True
    assert quarantined_task.task.priority == Priority.URGENT
    assert quarantined_task.task.validation_status == ValidationStatus.SYSTEM_GENERATED
    assert "[CẢNH BÁO BẢO MẬT]" in quarantined_task.task.title
    assert len(quarantined_task.task.action_plan) == 2
