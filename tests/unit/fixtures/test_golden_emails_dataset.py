"""Unit tests for the 30-case Golden Dataset and Security Test Attachments."""

from __future__ import annotations

import json
from pathlib import Path

from cowork_agent.domain.target_contracts import ThreatCategory, ThreatLevel
from cowork_agent.integrations.security.magic_inspector import inspect_attachment_file
from cowork_agent.integrations.security.url_inspector import inspect_url

ROOT_DIR = Path(__file__).resolve().parents[3]
DATASET_PATH = ROOT_DIR / "data" / "golden_emails_dataset.json"
MD_PATH = ROOT_DIR / "data" / "golden_emails_to_send.md"


def test_golden_dataset_file_exists_and_contains_30_cases():
    assert DATASET_PATH.exists(), f"Golden dataset JSON not found at {DATASET_PATH}"
    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    assert isinstance(cases, list)
    assert len(cases) == 30, f"Expected 30 golden test cases, found {len(cases)}"


def test_golden_dataset_all_attachment_paths_exist():
    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    attachment_count = 0
    for case in cases:
        for att in case.get("attachments", []):
            rel_path = att.get("file_path", att.get("path", ""))
            abs_path = ROOT_DIR / rel_path
            assert abs_path.exists(), f"Attachment {rel_path} does not exist on disk"
            assert abs_path.stat().st_size > 0, f"Attachment {rel_path} is empty"
            attachment_count += 1

    assert attachment_count >= 10, f"Expected at least 10 attachments, found {attachment_count}"


def test_golden_dataset_target_rag_documents_exist():
    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    for case in cases:
        for doc_rel in case.get("target_documents", []):
            doc_path = ROOT_DIR / doc_rel
            assert doc_path.exists(), f"Target RAG document {doc_rel} not found in {ROOT_DIR}"


def test_golden_dataset_security_threat_invariants():
    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    for case in cases:
        threat_level = case["threat_level"]
        threat_cat = case["threat_category"]
        quarantined = case["quarantined"]

        # Valid ThreatLevel & ThreatCategory enum representation
        assert ThreatLevel(threat_level.lower())
        assert ThreatCategory(threat_cat.lower())

        if threat_level in ("MALICIOUS", "BLOCKED"):
            assert (
                quarantined is True
            ), f"Case {case['id']} is {threat_level} but quarantined is False"
            assert case["expected_output"]["priority"] == "URGENT"

        if threat_level == "CLEAN":
            assert quarantined is False


def test_homograph_and_scheme_security_detection_on_dataset_cases():
    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    case_11 = next(c for c in cases if c["id"] == "email_11")
    assert case_11["quarantined"] is True
    report_11 = inspect_url("https://g\u043e\u043egle.com/workspace-security/verify-identity?token=948291a0")
    assert report_11.threat_level == ThreatLevel.MALICIOUS
    assert report_11.threat_category == ThreatCategory.HOMOGRAPH_SPOOF

    case_25 = next(c for c in cases if c["id"] == "email_25")
    assert case_25["quarantined"] is True
    report_25 = inspect_url("javascript:alert(document.cookie)")
    assert report_25.threat_level == ThreatLevel.BLOCKED
    assert report_25.threat_category == ThreatCategory.PARSER_EXPLOIT


def test_attachment_security_detection_on_new_dataset_cases():
    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    # Email 26: Zip Bomb
    case_26 = next(c for c in cases if c["id"] == "email_26")
    assert case_26["threat_category"] == "ZIP_BOMB"
    zip_path = ROOT_DIR / case_26["attachments"][0]["file_path"]
    rep_26 = inspect_attachment_file(zip_path)
    assert rep_26.threat_level == ThreatLevel.BLOCKED
    assert rep_26.threat_category == ThreatCategory.ZIP_BOMB

    # Email 27: Masquerading PE disguised as PDF
    case_27 = next(c for c in cases if c["id"] == "email_27")
    pe_path = ROOT_DIR / case_27["attachments"][0]["file_path"]
    rep_27 = inspect_attachment_file(pe_path)
    assert rep_27.threat_level == ThreatLevel.MALICIOUS
    assert rep_27.threat_category == ThreatCategory.MALWARE

    # Email 29: Clean multi-attachment
    case_29 = next(c for c in cases if c["id"] == "email_29")
    for att in case_29["attachments"]:
        rep = inspect_attachment_file(ROOT_DIR / att["file_path"])
        assert rep.threat_level == ThreatLevel.CLEAN


def test_markdown_doc_contains_all_30_emails():
    assert MD_PATH.exists()
    content = MD_PATH.read_text(encoding="utf-8")

    for i in range(1, 31):
        pattern = f"EMAIL {i:02d}:"
        assert pattern in content, f"Missing {pattern} in {MD_PATH}"
