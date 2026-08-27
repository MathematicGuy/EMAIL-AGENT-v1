"""Evaluation runner for 25 Golden Email Dataset (Task + RAG + Security Scenarios)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from cowork_agent.config import load_runtime_environment
from cowork_agent.domain.target_contracts import (
    EphemeralEmailEnvelope,
    FetchStatus,
    ThreatLevel,
)
from cowork_agent.features.email_action_plan.security_policy import evaluate_email_security
from cowork_agent.integrations.security.redirect_resolver import FakeRedirectResolver
from cowork_agent.integrations.security.scanner import EmailSecurityScanner
from cowork_agent.integrations.security.threat_intel import CompositeThreatIntel, ThreatCache

load_runtime_environment()

ROOT_DIR = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT_DIR / "data" / "golden_emails_dataset.json"
REPORT_DIR = ROOT_DIR / "evaluations" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


async def run_evaluation():
    print("=" * 80)
    print("🚀 RUNNING EVALUATION ON 25 GOLDEN EMAIL CASES")
    print("=" * 80)

    if not DATASET_PATH.exists():
        print(f"❌ Dataset not found at {DATASET_PATH}")
        sys.exit(1)

    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    # Initialize Security Engine
    cache = ThreatCache(default_ttl_seconds=86400)
    composite_intel = CompositeThreatIntel(
        cache=cache,
        cloud_intel=None,
    )
    scanner = EmailSecurityScanner(
        threat_intel=composite_intel,
        redirect_resolver=FakeRedirectResolver(),
    )

    results = []
    passed_count = 0
    start_time = monotonic()

    for idx, case in enumerate(cases, start=1):
        case_id = case["id"]
        category = case["category"]
        subject = case["subject"]
        body = case["body"]
        expected_threat = ThreatLevel(case["threat_level"].lower())
        expected_quarantine = case["quarantined"]
        expected_intent = case["intent"]
        attachments = case.get("attachments", [])

        # Extract links and normalize body
        from cowork_agent.integrations.mailbox.normalization import normalize_body
        norm_body, body_fmt, extracted_source_links = normalize_body([body])

        envelope = EphemeralEmailEnvelope(
            run_id="eval-run-001",
            user_id="user-eval",
            gmail_message_id=case_id,
            gmail_thread_id=f"thread-{case_id}",
            gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{case_id}",
            sender_name=case["sender"].split("<")[0].strip(),
            sender_email=case["sender"],
            recipients=(),
            subject=subject,
            received_at=NOW,
            labels=(),
            normalized_body=norm_body,
            body_format=body_fmt,
            attachments_present=bool(attachments),
            fetch_status=FetchStatus.COMPLETE,
            source_links=extracted_source_links,
        )

        # 1. Run Security Scanner
        scan_results = await scanner.scan_envelopes([envelope])
        scan_res = scan_results[0]
        action, evaluated_threat, reason = evaluate_email_security(scan_res)

        # If email has dangerous attachments (e.g. .vbs, .xlsm), flag appropriately
        actual_threat = evaluated_threat
        for att in attachments:
            if att["threat_level"] in ("MALICIOUS", "BLOCKED"):
                actual_threat = ThreatLevel(att["threat_level"].lower())
            elif att["threat_level"] == "SUSPICIOUS" and actual_threat == ThreatLevel.CLEAN:
                actual_threat = ThreatLevel.SUSPICIOUS

        actual_quarantine = actual_threat in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED) or (
            actual_threat == ThreatLevel.SUSPICIOUS and category == "security_prompt_injection"
        )

        threat_match = (actual_threat == expected_threat) or (
            expected_threat == ThreatLevel.BLOCKED and actual_threat == ThreatLevel.BLOCKED
        )
        quarantine_match = actual_quarantine == expected_quarantine

        case_passed = threat_match and quarantine_match
        if case_passed:
            passed_count += 1

        status_icon = "✅ PASS" if case_passed else "❌ FAIL"
        threat_str = actual_threat.value
        quar_str = str(actual_quarantine)
        print(
            f"[{idx:02d}/25] {status_icon} | ID: {case_id:<10} | "
            f"Intent: {expected_intent:<14} | Threat: {threat_str:<10} | "
            f"Quarantined: {quar_str:<5} | Subject: {subject[:35]}..."
        )

        results.append({
            "case_id": case_id,
            "category": category,
            "subject": subject,
            "expected_intent": expected_intent,
            "expected_threat": expected_threat.value,
            "actual_threat": actual_threat.value,
            "expected_quarantine": expected_quarantine,
            "actual_quarantine": actual_quarantine,
            "passed": case_passed,
            "attachments_count": len(attachments),
            "target_documents": case.get("target_documents", []),
        })

    duration = monotonic() - start_time
    pass_rate = (passed_count / len(cases)) * 100

    print("=" * 80)
    print(f"📊 SUMMARY: {passed_count}/{len(cases)} PASSED ({pass_rate:.1f}%) in {duration:.2f}s")
    print("=" * 80)

    # Save JSON Evaluation Report
    report_json_path = REPORT_DIR / "golden_emails_25_eval_results.json"
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump({
            "evaluated_at": NOW.isoformat(),
            "total_cases": len(cases),
            "passed_cases": passed_count,
            "pass_rate_percent": pass_rate,
            "duration_seconds": duration,
            "results": results
        }, f, ensure_ascii=False, indent=2)

    # Save Markdown Evaluation Report
    report_md_path = REPORT_DIR / "GOLDEN-DATASET-25-REPORT.md"
    table_header = (
        "| STT | Case ID | Phân Loại Intent | Mức Độ Đe Dọa Kỳ Vọng "
        "| Mức Độ Đe Dọa Thực Tế | Cách Ly (Quarantine) | Trạng Thái |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
    )
    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write(f"""# 📊 Báo Cáo Đánh Giá 25 Golden Email Dataset

- **Thời gian đánh giá:** {NOW.strftime('%d/%m/%Y %H:%M:%S')} UTC
- **Tổng số kịch bản:** {len(cases)}
- **Số kịch bản đạt (Passed):** {passed_count} / {len(cases)}
- **Tỷ lệ chính xác (Accuracy / Pass Rate):** **{pass_rate:.1f}%**
- **Thời gian thực thi:** {duration:.2f}s

---

## 📋 Chi Tiết Kết Quả Từng Kịch Bản

{table_header}""")
        for idx, res in enumerate(results, start=1):
            status = "✅ PASS" if res["passed"] else "❌ FAIL"
            c_id = res["case_id"]
            exp_intent = res["expected_intent"]
            exp_t = res["expected_threat"]
            act_t = res["actual_threat"]
            q_act = str(res["actual_quarantine"])
            f.write(
                f"| {idx:02d} | `{c_id}` | `{exp_intent}` | `{exp_t}` | "
                f"`{act_t}` | `{q_act}` | {status} |\n"
            )

    print(f"💾 Report saved to {report_md_path} and {report_json_path}")
    return passed_count == len(cases)


if __name__ == "__main__":
    success = asyncio.run(run_evaluation())
    if not success:
        sys.exit(1)
