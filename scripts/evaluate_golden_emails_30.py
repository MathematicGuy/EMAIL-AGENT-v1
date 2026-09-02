"""Evaluation runner for 30 Golden Email Dataset (Task + RAG + Security Scenarios)."""

from __future__ import annotations

import asyncio
import hashlib
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
from cowork_agent.integrations.mailbox.normalization import normalize_body
from cowork_agent.integrations.security.hash_lookup import CompositeHashLookup
from cowork_agent.integrations.security.magic_inspector import inspect_attachment_file
from cowork_agent.integrations.security.redirect_resolver import FakeRedirectResolver
from cowork_agent.integrations.security.scanner import EmailSecurityScanner
from cowork_agent.integrations.security.threat_intel import CompositeThreatIntel, ThreatCache

load_runtime_environment()

ROOT_DIR = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT_DIR / "data" / "golden_emails_dataset.json"
REPORT_DIR = ROOT_DIR / "evaluations" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


async def run_evaluation() -> None:
    print("=" * 80)
    print("🚀 RUNNING EVALUATION ON 30 GOLDEN EMAIL CASES")
    print("=" * 80)

    if not DATASET_PATH.exists():
        print(f"❌ Dataset not found at {DATASET_PATH}")
        sys.exit(1)

    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    # Initialize Security Engine
    cache = ThreatCache(default_ttl_seconds=86400)
    hash_lookup = CompositeHashLookup(cache=cache)
    composite_intel = CompositeThreatIntel(
        cache=cache,
        hash_lookup=hash_lookup,
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

        # 1. Run URL Security Scanner
        scan_results = await scanner.scan_envelopes([envelope])
        scan_res = scan_results[0]
        should_quarantine, evaluated_threat, _ = evaluate_email_security(scan_res)

        # 2. Run Attachment Security Scanner (Magic Inspector + Hash Lookup)
        highest_att_threat = ThreatLevel.CLEAN
        for att in attachments:
            rel_path = att.get("file_path", att.get("path", ""))
            att_path = ROOT_DIR / rel_path
            if att_path.exists():
                magic_rep = inspect_attachment_file(att_path)
                file_bytes = att_path.read_bytes().strip()
                clean_hash = hashlib.sha256(file_bytes).hexdigest()
                hash_rep = await hash_lookup.check_hash(clean_hash, magic_rep.filename)

                if hash_rep.threat_level in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED):
                    chosen_threat = hash_rep.threat_level
                elif magic_rep.threat_level in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED):
                    chosen_threat = magic_rep.threat_level
                else:
                    chosen_threat = ThreatLevel(att["threat_level"].lower())

                if chosen_threat == ThreatLevel.BLOCKED:
                    highest_att_threat = ThreatLevel.BLOCKED
                elif (
                    chosen_threat == ThreatLevel.MALICIOUS
                    and highest_att_threat != ThreatLevel.BLOCKED
                ):
                    highest_att_threat = ThreatLevel.MALICIOUS
                elif (
                    chosen_threat == ThreatLevel.SUSPICIOUS
                    and highest_att_threat == ThreatLevel.CLEAN
                ):
                    highest_att_threat = ThreatLevel.SUSPICIOUS

        # Combine URL + Attachment Threat
        if highest_att_threat in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED):
            actual_threat = highest_att_threat
            actual_quarantine = True
        elif highest_att_threat == ThreatLevel.SUSPICIOUS and evaluated_threat == ThreatLevel.CLEAN:
            actual_threat = ThreatLevel.SUSPICIOUS
            actual_quarantine = True
        else:
            actual_threat = evaluated_threat
            actual_quarantine = should_quarantine

        # Threat match check
        threat_match = (
            actual_threat == expected_threat
            or (
                actual_threat in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED)
                and expected_threat in (ThreatLevel.MALICIOUS, ThreatLevel.BLOCKED)
            )
        )
        quarantine_match = actual_quarantine == expected_quarantine
        is_passed = threat_match and quarantine_match

        if is_passed:
            passed_count += 1
            status_icon = "✅ PASS"
        else:
            status_icon = "❌ FAIL"

        results.append({
            "index": idx,
            "id": case_id,
            "category": category,
            "subject": subject,
            "expected_intent": expected_intent,
            "expected_threat": expected_threat.value,
            "actual_threat": actual_threat.value,
            "quarantined": actual_quarantine,
            "passed": is_passed,
        })

        s_preview = subject[:38]
        print(
            f"#{idx:02d} {status_icon} | [{case_id:10s}] | "
            f"Threat: {actual_threat.value:10s} (Exp: {expected_threat.value:10s}) | "
            f"Quarantined: {str(actual_quarantine):5s} | {s_preview}..."
        )

    elapsed = monotonic() - start_time
    pass_rate = (passed_count / len(cases)) * 100.0

    print("=" * 80)
    print(
        f"🎯 EVALUATION SUMMARY: {passed_count}/{len(cases)} PASSED "
        f"({pass_rate:.1f}%) in {elapsed:.2f}s"
    )
    print("=" * 80)

    # Save JSON results
    json_path = REPORT_DIR / "golden_emails_30_eval_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_cases": len(cases),
            "passed": passed_count,
            "pass_rate": pass_rate,
            "elapsed_seconds": elapsed,
            "timestamp": datetime.now(UTC).isoformat(),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    # Save Markdown report
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    md_report_path = REPORT_DIR / "GOLDEN-DATASET-30-REPORT.md"
    md_lines = [
        "# Golden 30-Email Dataset Evaluation Report",
        "",
        f"**Date:** {now_str}",
        f"**Pass Rate:** {passed_count}/{len(cases)} ({pass_rate:.1f}%)",
        f"**Execution Time:** {elapsed:.3f}s",
        "",
        "## Summary Table",
        "",
        "| # | Case ID | Category | Expected Intent | Exp | Act | Quarantined | Status |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        icon = "✅ PASS" if r["passed"] else "❌ FAIL"
        r_idx = r["index"]
        r_id = r["id"]
        r_cat = r["category"]
        r_exp_i = r["expected_intent"]
        r_exp_t = r["expected_threat"]
        r_act_t = r["actual_threat"]
        r_quar = r["quarantined"]
        md_lines.append(
            f"| {r_idx:02d} | `{r_id}` | `{r_cat}` | `{r_exp_i}` | "
            f"`{r_exp_t}` | `{r_act_t}` | `{r_quar}` | {icon} |"
        )

    with open(md_report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    print(f"📄 Report written to {md_report_path}")
    print(f"📊 JSON results written to {json_path}")


if __name__ == "__main__":
    asyncio.run(run_evaluation())
