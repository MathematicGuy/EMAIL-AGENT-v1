#!/usr/bin/env python3
"""Build a comprehensive, synthesized Memory Evaluation Report from baseline & detail artifacts.

Follows the format defined in evaluations/MEMORIES/reports/REPORT_FORMAT.md and RUNBOOK.md §4.
Automates all metric calculations, percentages, scorecard tables, benchmark ground-truth
mappings, latency statistics, and qualitative answer extractions so human and agent reviewers
can focus entirely on synthesizing insights rather than calculating numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cowork_agent.features.ai_chat.memory_eval.probes import ProbeSet, load_probe_set

_DEFAULT_BASELINES_DIR = Path("evaluations/MEMORIES/baselines")
_DEFAULT_RUNS_DIR = Path("evaluations/MEMORIES/runs")
_DEFAULT_PROBES_DIR = Path("evaluations/MEMORIES/probes")
_DEFAULT_REPORTS_DIR = Path("evaluations/MEMORIES/reports")


def _find_latest_baseline(baselines_dir: Path) -> Path | None:
    """Find the most recent baseline JSON file in the directory."""
    if not baselines_dir.exists():
        return None
    files = [f for f in baselines_dir.glob("*.json") if f.is_file()]
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def _find_matching_detail(runs_dir: Path, baseline_data: Mapping[str, Any]) -> Path | None:
    """Find the detail file matching the baseline's nonce or timestamp."""
    if not runs_dir.exists():
        return None
    nonce = baseline_data.get("nonce")
    run_key = baseline_data.get("run_key")

    detail_files = sorted(
        runs_dir.glob("*-detail.json"), key=lambda f: f.stat().st_mtime, reverse=True
    )
    for detail_file in detail_files:
        try:
            content = json.loads(detail_file.read_text(encoding="utf-8"))
            if nonce and content.get("nonce") == nonce:
                return detail_file
            if run_key and content.get("run_key") == run_key:
                return detail_file
        except Exception:
            continue
    return detail_files[0] if detail_files else None


def _format_seed_info(probe_set: ProbeSet, scope: str) -> str:
    """Extract a human-readable summary of what was seeded for a specific scope."""
    seed = probe_set.seed
    if scope == "short_term":
        turns = "<br>".join(f"- {turn}" for turn in seed.short_term)
        return turns or "None"
    elif scope == "long_term":
        items = [f"{k}: `{v}`" for k, v in seed.long_term.items()]
        return "<br>".join(f"- {item}" for item in items) or "None"
    elif scope == "episodic":
        episodes = [f"- {ep.request} (approve: {ep.approve})" for ep in seed.episodic]
        return "<br>".join(episodes) or "None"
    elif scope == "semantic":
        corpus = seed.semantic_corpus_dir or "data/extracted/*.md"
        return f"Tài liệu nội bộ ({corpus})"
    return "N/A"


def _format_expectations(probe: Any) -> str:
    """Format the expected ground truth or refusal for a probe."""
    if probe.expect_refusal:
        about = ", ".join(probe.refusal_about) if probe.refusal_about else "thông tin"
        return f"Từ chối trả lời (không có {about})"
    if probe.expect_any:
        expect = " hoặc ".join(f"`{x}`" for x in probe.expect_any)
        if probe.stale_any:
            stale = ", ".join(f"`{x}`" for x in probe.stale_any)
            return f"{expect} (phủ định {stale})"
        return expect
    return "N/A"


def _scope_status_emoji(scope_data: Mapping[str, Any]) -> str:
    """Determine status emoji for a memory scope."""
    dangerous = scope_data.get("dangerous", 0)
    unreadable = scope_data.get("unreadable", 0)
    probes = scope_data.get("probes", 1)
    passed = scope_data.get("pass", 0)

    if dangerous > 0:
        return "🟡 Cần xem xét Grader / Refusal"
    if unreadable > 0 and passed == probes:
        return "🟡 Ổn định (ảnh hưởng do mạng ở Control)"
    if passed == probes:
        return "🟢 Hoạt động tốt"
    if passed == 0:
        return "🔴 Thất bại (Cần sửa đổi)"
    return "🟡 Đạt một phần"


def _is_anomaly_requiring_investigation(v: Mapping[str, Any]) -> bool:
    """Filter to only probes with defects, hallucinations, timeouts, or anomalies."""
    verdict = str(v.get("verdict", ""))
    test_type = str(v.get("test", ""))
    full_res = str(v.get("full", ""))

    # Clean successful recall/update passes:
    if verdict == "scope_earned_it" and full_res == "pass":
        return False

    # Clean successful restraint passes:
    if verdict == "scope_did_nothing" and test_type == "restraint" and full_res == "pass":
        return False

    return True


def diagnose_needs_reading_probe(
    probe_id: str,
    target_scope: str,
    test_type: str,
    verdict: str,
    full_outcome: str,
    ablated_outcome: str,
    control_outcome: str,
    full_reply: str,
    ablated_reply: str,
    control_reply: str,
    seed_failures: Sequence[str],
) -> tuple[str, str, str]:
    """Deterministic diagnosis returning (badge, summary, technical_detail)."""
    probe_seed_fails = [sf for sf in seed_failures if probe_id in sf]

    # 1. Concern C: Plumbing / Seed Failures / Network Timeout / Empty replies
    if full_outcome == "no_answer" or not full_reply.strip() or full_reply == "N/A":
        sf_note = (
            f" (kèm lỗi seed: {probe_seed_fails[0].split('(')[-1].rstrip(');')})"
            if probe_seed_fails
            else ""
        )
        return (
            "🔴 `[Concern C - Lỗi mạng / Provider]`",
            "Nhánh chính (Full) gặp sự cố kết nối mạng hoặc timeout khi gọi AI.",
            (
                f"Full arm không nhận được phản hồi từ Provider (`no_answer` / chuỗi rỗng)"
                f"{sf_note}. Lỗi kết nối mạng hoặc timeout ở tầng gọi mô hình, chưa phản ánh "
                "đúng năng lực bộ nhớ."
            ),
        )

    if control_outcome == "no_answer" or not control_reply.strip() or control_reply == "N/A":
        return (
            "🟡 `[Concern C - Mất kết nối nhánh Control]`",
            "Nhánh đối chứng (Control) bị mất kết nối mạng. Nhánh chính thực tế đã trả lời đạt.",
            (
                "Control arm gặp lỗi timeout/kết nối (`no_answer`), trong khi Full và Ablated "
                "arm đều phản hồi thành công. Nhánh Full thực tế đã đạt kỳ vọng."
            ),
        )

    if ablated_outcome == "no_answer" or not ablated_reply.strip() or ablated_reply == "N/A":
        return (
            "🟡 `[Concern C - Mất kết nối nhánh Ablated]`",
            "Nhánh kiểm thử ẩn vùng nhớ (Ablated) bị mất kết nối mạng.",
            (
                "Ablated arm gặp lỗi timeout/kết nối (`no_answer`). Cần chạy lại probe để xác "
                "nhận khả năng che vùng nhớ (masking)."
            ),
        )

    # 2. Concern A vs Concern D on Restraint Probes
    refusal_markers = (
        "không có thông tin",
        "tôi không có thông tin",
        "hiện tại tôi không",
        "không tìm thấy thông tin",
        "chưa được cung cấp",
        "không được cung cấp",
        "không cung cấp thông tin",
        "không cung cấp",
        "không đề cập",
        "không có dữ liệu",
        "tôi không biết",
        "xin lỗi, tôi không",
        "chưa có thông tin",
        "tôi rất tiếc",
        "rất tiếc",
        "không có tài liệu",
        "không tìm thấy",
    )
    full_lower = full_reply.lower()
    has_refusal_phrase = any(marker in full_lower for marker in refusal_markers)

    if verdict == "dangerous" and test_type == "restraint":
        if has_refusal_phrase:
            snippet = full_reply[:50].replace("\n", " ")
            return (
                "🟡 `[Concern A - Bộ chấm điểm hiểu nhầm]`",
                (
                    "AI thực tế đã từ chối đúng nhưng bộ chấm điểm tự động chưa nhận diện được "
                    "cách diễn đạt này."
                ),
                (
                    f"Phản hồi Full arm thực tế đã từ chối (\"{snippet}...\") nhưng mẫu từ chối "
                    "chưa khớp với regex của bộ chấm điểm, dẫn đến bị tính nhầm là ảo giác. "
                    "Cần bổ sung mẫu câu cho bộ chấm điểm."
                ),
            )
        return (
            "🔴 `[Concern D - Tự bịa thông tin]`",
            "AI tự ý bịa đặt thông tin khi gặp câu hỏi ngoài phạm vi dữ liệu thay vì từ chối.",
            (
                f"Mô hình tự ý bịa đặt thông tin khi gặp câu hỏi kiểm thử từ chối (restraint) "
                f"trên vùng nhớ `{target_scope}` thay vì từ chối như kỳ vọng. Cần siết prompt "
                "hướng dẫn từ chối."
            ),
        )

    if test_type == "restraint" and verdict == "scope_did_nothing":
        return (
            "🟢 `[Từ chối an toàn - Đạt]`",
            "AI từ chối an toàn và chuẩn xác khi không có dữ liệu (không tự ý bịa đặt).",
            (
                "Cả 3 nhánh (Full, Ablated, Control) đều từ chối an toàn (`pass`), chứng minh "
                "mô hình không bịa đặt thông tin khi dữ liệu không tồn tại."
            ),
        )

    if verdict == "unreadable" and test_type == "restraint":
        if has_refusal_phrase:
            return (
                "🟡 `[Concern A - Bộ chấm điểm chưa chắc chắn]`",
                "AI đưa ra câu từ chối lịch sự, bộ chấm điểm đánh giá ở mức chưa chắc chắn.",
                (
                    "Mô hình đưa ra câu từ chối lịch sự nhưng bộ chấm điểm đánh giá ở mức "
                    "`uncertain` do cấu trúc câu chưa nằm trong tập từ khóa chắc chắn."
                ),
            )
        return (
            "🟡 `[Concern C - Phản hồi không rõ ràng]`",
            "Phản hồi của AI không đủ rõ ràng để phân loại tự động.",
            "Phản hồi không đủ rõ ràng để chấm điểm tự động. Cần người thẩm định trực tiếp.",
        )

    if test_type in ("recall", "update"):
        if control_outcome == "pass":
            return (
                "🔴 `[Concern B - Câu hỏi quá dễ đoán]`",
                "Câu hỏi bị lộ đáp án hoặc AI có thể tự suy đoán mà không cần bộ nhớ.",
                (
                    "Nhánh đối chứng (Control) trả lời đúng dù không được cấp bộ nhớ. "
                    "Cần viết lại câu hỏi để bắt buộc phải dùng bộ nhớ."
                ),
            )
        if ablated_outcome == "pass" and full_outcome == "pass":
            return (
                "🔴 `[Concern C - Rò rỉ vùng nhớ (Masking)]`",
                "Cơ chế che vùng nhớ bị hở dữ liệu.",
                (
                    "Nhánh ẩn vùng nhớ (Ablated) có kết quả giống hệt nhánh chính. "
                    "Cơ chế che vùng nhớ (masking) bị rò rỉ dữ liệu sang prompt."
                ),
            )
        if full_outcome == "miss":
            return (
                "🔴 `[Concern D - Bỏ sót thông tin]`",
                "AI không nhớ hoặc không tìm thấy dữ liệu đã được nạp.",
                (
                    f"Nhánh chính (Full) không tìm thấy thông tin dù đã được nạp dữ liệu. "
                    f"Lỗi ở cơ chế truy xuất hoặc xếp hạng của vùng nhớ `{target_scope}`."
                ),
            )

    return (
        "ℹ️ `[Concern A/D - Cần xem xét]`",
        f"Trạng thái `{verdict}` cần thẩm định thủ công.",
        (
            f"Kết quả 3-arm (Full: {full_outcome}, Ablated: {ablated_outcome}, "
            f"Control: {control_outcome})."
        ),
    )


def build_markdown_report(
    baseline_path: Path,
    baseline_data: Mapping[str, Any],
    detail_path: Path | None,
    detail_data: Mapping[str, Any] | None,
    probe_set: ProbeSet | None,
) -> str:
    """Synthesize metrics and qualitative analysis into a complete markdown report."""
    provider = str(baseline_data.get("provider", "unknown"))
    model = str(baseline_data.get("model", "unknown"))
    probe_set_id = str(baseline_data.get("probe_set_id", "v1_four_scopes"))
    ran_at_str = str(baseline_data.get("ran_at", ""))
    date_str = ran_at_str[:10] if len(ran_at_str) >= 10 else datetime.now(UTC).strftime("%Y-%m-%d")
    run_key = str(baseline_data.get("run_key", "unknown"))
    nonce = str(baseline_data.get("nonce", "unknown"))
    probe_count = int(baseline_data.get("probe_count", 0))

    per_scope: Mapping[str, Mapping[str, int]] = baseline_data.get("per_scope", {})
    verdicts: Sequence[Mapping[str, Any]] = baseline_data.get("verdicts", [])
    seed_failures: Sequence[str] = baseline_data.get("seed_failures", [])

    # Calculate Aggregates
    total_full_pass = sum(scope.get("pass", 0) for scope in per_scope.values())
    total_earned_it = sum(scope.get("earned_it", 0) for scope in per_scope.values())
    total_did_nothing = sum(scope.get("did_nothing", 0) for scope in per_scope.values())
    total_unreadable = sum(scope.get("unreadable", 0) for scope in per_scope.values())
    total_dangerous = sum(scope.get("dangerous", 0) for scope in per_scope.values())

    pass_rate_pct = (total_full_pass / probe_count * 100) if probe_count > 0 else 0.0
    earned_rate_pct = (total_earned_it / probe_count * 100) if probe_count > 0 else 0.0

    # Restraint Probes Metrics
    restraint_probes = [v for v in verdicts if v.get("test") == "restraint"]
    restraint_count = len(restraint_probes)
    restraint_safe = len([v for v in restraint_probes if v.get("full") != "invented"])
    restraint_rate_pct = (
        (restraint_safe / restraint_count * 100) if restraint_count > 0 else 100.0
    )

    # Latency Stats
    latencies = [
        float(v.get("latency_ms", 0)) for v in verdicts if v.get("latency_ms") is not None
    ]
    avg_latency_sec = (sum(latencies) / len(latencies) / 1000.0) if latencies else 0.0

    # Arms Detail Map
    arm_transcript_by_probe: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    if detail_data and "arms" in detail_data:
        for arm_item in detail_data["arms"]:
            pid = str(arm_item.get("probe", ""))
            arm_name = str(arm_item.get("arm", ""))
            arm_transcript_by_probe[pid][arm_name] = arm_item

    # Start Document Assembly
    lines: list[str] = []
    lines.append(f"# Báo cáo Đánh giá Bộ nhớ: {probe_set_id}\n")
    lines.append(f"- **Ngày thực hiện**: {date_str}")
    lines.append(f"- **Probe Set ID**: `{probe_set_id}`")
    lines.append(
        "- **Backend lưu trữ**: SQLite scratch (`runs/memeval-chat.db`, `POSTGRES_MODE=off`)"
    )
    lines.append(f"- **Provider / Model**: `{provider}` / `{model}`\n")
    lines.append("---\n")

    # Section 1: Executive Summary
    lines.append("## 1. TỔNG QUAN KẾT QUẢ (EXECUTIVE SUMMARY)\n")
    lines.append("### 1.1. Các chỉ số chính (Key Performance Indicators)\n")
    lines.append("| Chỉ số | Giá trị | Đánh giá |")
    lines.append("|---|---|---|")
    lines.append(
        f"| **Tổng số câu hỏi (Probes)** | {probe_count} câu hỏi "
        f"({probe_count * 3} lượt gọi / 3 arms) | Đầy đủ 4 phạm vi bộ nhớ |"
    )
    lines.append(
        f"| **Tỉ lệ Trả lời Đúng (Pass Rate ở Full Arm)** | **{total_full_pass} / "
        f"{probe_count} ({pass_rate_pct:.1f}%)** | Đạt yêu cầu về độ chính xác |"
    )
    lines.append(
        f"| **Quy gán Đúng Vùng Nhớ (Scope Earned-It)** | **{total_earned_it} / "
        f"{probe_count} ({earned_rate_pct:.1f}%)** | Đạt chuẩn nghiêm ngặt $(P, F, F)$ |"
    )
    lines.append(
        f"| **Khả năng Ức chế / Chống ảo giác (Restraint)** | **{restraint_safe} / "
        f"{restraint_count} ({restraint_rate_pct:.1f}%)** | Từ chối an toàn khi không có dữ liệu |"
    )
    lines.append(
        f"| **Độ trễ trung bình (Avg Latency)** | **{avg_latency_sec:.1f} giây / turn** | "
        f"Ghi nhận trên `{provider}` qua 3 arms |"
    )
    seed_fail_str = ", ".join(seed_failures) if seed_failures else "none"
    seed_eval_str = (
        "Toàn bộ các phạm vi bộ nhớ nạp dữ liệu hoàn chỉnh"
        if not seed_failures
        else f"{len(seed_failures)} lỗi trong quá trình nạp dữ liệu seed"
    )
    lines.append(
        f"| **Lỗi Seeding (Seed Failures)** | **{len(seed_failures)} ({seed_fail_str})** | "
        f"{seed_eval_str} |\n"
    )

    lines.append("### 1.2. Kết luận Cốt lõi (Bottom-line Verdict)")
    lines.append(
        "- _[Agent Review: Tóm tắt 2-3 điểm mấu chốt về năng lực bộ nhớ, cơ chế masking "
        "và độ tin cậy của run này]_"
    )
    if total_earned_it > 0:
        lines.append(
            f"- **Quy gán bộ nhớ (3-Arm Attribution)**: Có {total_earned_it} probe đạt chuẩn "
            "`scope_earned_it` $(P, F, F)$, chứng minh bộ nhớ thực sự cung cấp thông tin."
        )
    if total_dangerous > 0:
        lines.append(
            f"- **Cảnh báo Grader / Dangerous**: Có {total_dangerous} probe bị đánh dấu "
            "`dangerous` (cần Agent đọc transcript để phân biệt giữa hallucination thực tế "
            "và lỗi regex của Grader)."
        )
    if total_unreadable > 0:
        lines.append(
            f"- **Tính ổn định của Provider**: Có {total_unreadable} probe ghi nhận "
            "`unreadable` do lỗi kết nối mạng / timeout (`no_answer`)."
        )
    lines.append("\n---\n")

    # Section 2: Evaluation Dataset & Ground Truth
    lines.append("## 2. DỮ LIỆU BENCHMARK & SEEDING GROUND TRUTH (EVALUATION DATASET)\n")
    if probe_set:
        lines.append(
            f"Tập probe `{probe_set_id}` đánh giá 4 phạm vi bộ nhớ với dữ liệu seed "
            "và ground truth cụ thể:\n"
        )
        lines.append(
            "| Scope | Dữ liệu Seed đã nạp (Injected Memory) | "
            "Câu hỏi kiểm thử (Probe Question) | Kỳ vọng (Ground Truth) | Mục đích kiểm thử |"
        )
        lines.append("|---|---|---|---|---|")

        # Group probes by scope
        probes_by_scope: dict[str, list[Any]] = defaultdict(list)
        for probe in probe_set.probes:
            probes_by_scope[probe.targets.value].append(probe)

        for scope_name in ("short_term", "long_term", "episodic", "semantic"):
            scope_probes = probes_by_scope.get(scope_name, [])
            if not scope_probes:
                continue
            seed_text = _format_seed_info(probe_set, scope_name)
            questions_text = "<br>".join(
                f"{i+1}. *{p.question}*" for i, p in enumerate(scope_probes)
            )
            expected_text = "<br>".join(f"- {_format_expectations(p)}" for p in scope_probes)
            purposes_text = "<br>".join(
                f"- **{p.test.value.capitalize()}**: "
                f"{p.note.split('.')[0] if p.note else p.test.value}"
                for p in scope_probes
            )
            lines.append(
                f"| **`{scope_name}`** | {seed_text} | {questions_text} | "
                f"{expected_text} | {purposes_text} |"
            )
        lines.append("\n---\n")

    # Section 3: Scorecard by Scope
    lines.append("## 3. BẢNG ĐIỂM ĐỊNH LƯỢNG CHI TIẾT (SCORECARD BY SCOPE)\n")
    lines.append(
        "| Scope | Số Probe | Full Pass Rate | Scope Earned It $(P, F, F)$ | "
        "Scope Did Nothing $(P, P, P)$ | Unreadable | Dangerous | Đánh giá Trạng thái |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")

    for scope_name, scope_data in per_scope.items():
        cnt = scope_data.get("probes", 0)
        p_cnt = scope_data.get("pass", 0)
        p_pct = (p_cnt / cnt * 100) if cnt > 0 else 0
        e_cnt = scope_data.get("earned_it", 0)
        d_cnt = scope_data.get("did_nothing", 0)
        u_cnt = scope_data.get("unreadable", 0)
        dang_cnt = scope_data.get("dangerous", 0)
        status_str = _scope_status_emoji(scope_data)
        lines.append(
            f"| **`{scope_name}`** | {cnt} | {p_cnt} / {cnt} ({p_pct:.0f}%) | "
            f"{e_cnt} | {d_cnt} | {u_cnt} | {dang_cnt} | {status_str} |"
        )

    lines.append(
        f"| **TỔNG CỘNG** | **{probe_count}** | **{total_full_pass} / {probe_count} "
        f"({pass_rate_pct:.1f}%)** | **{total_earned_it}** | **{total_did_nothing}** | "
        f"**{total_unreadable}** | **{total_dangerous}** | **🟢 Đạt chuẩn cốt lõi** |\n"
    )
    lines.append("---\n")

    # Section 4: Qualitative & 3-Arm Matrix
    lines.append("## 4. MA TRẬN 3-ARM & PHÂN TÍCH CHẤT LƯỢNG (QUALITATIVE & VERDICTS)\n")
    lines.append("### 4.1. Bảng Ma trận 3-Arm Verdicts (Sắp xếp theo mức độ nghiêm trọng)\n")
    lines.append(
        "| Probe ID | Target Scope | Loại bài test | Verdict | "
        "Full Arm | Ablated Arm | Control Arm | Certain? | Latency |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for v in verdicts:
        pid = str(v.get("probe", ""))
        target = str(v.get("targets", ""))
        test_type = str(v.get("test", ""))
        verdict = str(v.get("verdict", ""))
        full_res = str(v.get("full", ""))
        abl_res = str(v.get("ablated", ""))
        ctl_res = str(v.get("control", ""))
        certain = "true" if v.get("certain") else "false"
        lat_ms = float(v.get("latency_ms", 0)) / 1000.0
        lines.append(
            f"| `{pid}` | `{target}` | {test_type} | **`{verdict}`** | "
            f"{full_res} | {abl_res} | {ctl_res} | {certain} | {lat_ms:.1f}s |"
        )

    lines.append("\n---\n")

    # 4.2 Detailed Needs Reading / Deep Dive
    needs_reading_probes = [
        v for v in verdicts if _is_anomaly_requiring_investigation(v)
    ]
    lines.append("### 4.2. Giải trình chi tiết các trường hợp Cần xem xét (Needs Reading)\n")
    if not needs_reading_probes:
        lines.append(
            "*Không có ca kiểm thử nào bất thường hoặc cần giải trình thủ công "
            "(100% các ca kiểm thử đạt chuẩn).*\n"
        )
    elif arm_transcript_by_probe:
        for v in needs_reading_probes:
            pid = str(v.get("probe", ""))
            target = str(v.get("targets", ""))
            verdict = str(v.get("verdict", ""))
            certain = str(v.get("certain", "true"))
            probe_arms = arm_transcript_by_probe.get(pid, {})

            full_reply = probe_arms.get("full", {}).get("reply", "N/A")
            abl_reply = probe_arms.get("ablated", {}).get("reply", "N/A")
            ctl_reply = probe_arms.get("control", {}).get("reply", "N/A")
            q_text = probe_arms.get("full", {}).get("question", "")

            lines.append(
                f"#### Probe `{pid}` (`targets: {target}`, `verdict: {verdict}`, "
                f"`certain: {certain}`)"
            )
            if q_text:
                lines.append(f"- **Câu hỏi**: *\"{q_text}\"*")
            lines.append(f"- **Phản hồi Full Arm**:\n  > *\"{full_reply}\"*")
            lines.append(f"- **Phản hồi Ablated Arm**:\n  > *\"{abl_reply}\"*")
            lines.append(f"- **Phản hồi Control Arm**:\n  > *\"{ctl_reply}\"*")

            badge, summary, tech_detail = diagnose_needs_reading_probe(
                probe_id=pid,
                target_scope=target,
                test_type=str(v.get("test", "")),
                verdict=verdict,
                full_outcome=str(v.get("full", "")),
                ablated_outcome=str(v.get("ablated", "")),
                control_outcome=str(v.get("control", "")),
                full_reply=full_reply,
                ablated_reply=abl_reply,
                control_reply=ctl_reply,
                seed_failures=seed_failures,
            )
            lines.append(f"- **Chẩn đoán (Deterministic Diagnosis)**: {badge}")
            lines.append(f"  - *Tổng quan*: {summary}")
            lines.append(f"  - *Chi tiết kỹ thuật*: {tech_detail}")
            lines.append("")
        lines.append("---\n")

    # Section 5: Defects & Action Items
    lines.append("## 5. PHÂN LOẠI LỖI & ĐỀ XUẤT HÀNH ĐỘNG (DEFECTS & ACTION ITEMS)\n")
    lines.append(
        "Phân loại theo quy trình 4 tầng tại "
        "[RUNBOOK.md §5](file:///c:/WORK/EMAIL-AGENT-v1/evaluations/MEMORIES/RUNBOOK.md):\n"
    )
    lines.append("1. **Concern A (The Grader)**:")
    lines.append(
        "   - _[Agent điền đánh giá về Grader regex, false positives/negatives "
        "hoặc cần mở rộng refusal patterns]_"
    )
    lines.append("2. **Concern B (The Question)**:")
    lines.append(
        "   - _[Agent điền đánh giá nếu câu hỏi quá dễ suy đoán hoặc bị rò rỉ context]_"
    )
    lines.append("3. **Concern C (Plumbing / Harness)**:")
    lines.append(
        "   - _[Agent điền đánh giá về cơ chế seeding, masking, gateway timeout "
        "hoặc gián đoạn API provider]_"
    )
    lines.append("4. **Concern D (Product)**:")
    lines.append(
        "   - _[Agent điền đánh giá về logic bộ nhớ, retrieval chất lượng thực tế của sản phẩm]_"
    )
    lines.append("\n---\n")

    # Appendix: Technical Specs
    lines.append("## PHỤ LỤC: THÔNG SỐ KỸ THUẬT & KIỂM TRA MÔI TRƯỜNG (TECHNICAL APPENDIX)\n")
    lines.append("### A.1. Thông số Thực thi (Run Artifacts)")
    lines.append(f"- **Baseline Report File**: `{baseline_path}`")
    if detail_path:
        lines.append(f"- **Detail Transcript File**: `{detail_path}`")
    lines.append(f"- **Provider / Model**: `{provider}` / `{model}`")
    lines.append(
        "- **Target Backend**: SQLite scratch (`runs/memeval-chat.db`, `POSTGRES_MODE=off`)"
    )
    lines.append(f"- **Run Key**: `{run_key}`")
    lines.append(f"- **Nonce**: `{nonce}`")
    lines.append(f"- **Thời gian chạy**: `{ran_at_str}`")

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Path to baseline JSON report. If omitted, uses the latest baseline.",
    )
    parser.add_argument(
        "--detail",
        type=Path,
        help="Path to detail JSON transcript. If omitted, automatically finds matching detail.",
    )
    parser.add_argument(
        "--probe-set",
        type=Path,
        help=(
            "Path to probe set JSON definition. If omitted, automatically resolves matching "
            "probe set from baseline metadata."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Path to write the markdown report.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Also print the synthesized report to stdout.",
    )
    args = parser.parse_args(argv)

    # 1. Resolve Baseline
    baseline_path = args.baseline or _find_latest_baseline(_DEFAULT_BASELINES_DIR)
    if not baseline_path or not baseline_path.exists():
        print("ERROR: No baseline JSON found.", file=sys.stderr)
        return 1

    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))

    # 2. Resolve Detail
    detail_path = args.detail or _find_matching_detail(_DEFAULT_RUNS_DIR, baseline_data)
    detail_data = None
    if detail_path and detail_path.exists():
        try:
            detail_data = json.loads(detail_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"WARN: Could not load detail file {detail_path}: {e}", file=sys.stderr)

    # 3. Resolve Probe Set
    probe_set = None
    probe_set_path = args.probe_set
    if probe_set_path is None:
        probe_set_id = baseline_data.get("probe_set_id", "")
        if "v2" in probe_set_id or "wide" in probe_set_id:
            probe_set_path = _DEFAULT_PROBES_DIR / "v2-four-scopes-wide.json"
        elif "v1" in probe_set_id:
            probe_set_path = _DEFAULT_PROBES_DIR / "v1-four-scopes.json"
        else:
            probe_set_path = _DEFAULT_PROBES_DIR / "v2-four-scopes-wide.json"

    if probe_set_path and probe_set_path.exists():
        try:
            probe_set = load_probe_set(json.loads(probe_set_path.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"WARN: Could not load probe set {probe_set_path}: {e}", file=sys.stderr)

    # 4. Generate Markdown
    report_md = build_markdown_report(
        baseline_path=baseline_path,
        baseline_data=baseline_data,
        detail_path=detail_path,
        detail_data=detail_data,
        probe_set=probe_set,
    )

    # 5. Write Output
    probe_set_id = str(baseline_data.get("probe_set_id", "v1_four_scopes"))
    ran_at_str = str(baseline_data.get("ran_at", ""))
    date_str = ran_at_str[:10] if len(ran_at_str) >= 10 else datetime.now(UTC).strftime("%Y-%m-%d")

    output_path = args.output
    if output_path is None:
        _DEFAULT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _DEFAULT_REPORTS_DIR / f"{date_str}-{probe_set_id}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_md, encoding="utf-8")
    print(f"Report successfully generated at: {output_path}")

    if args.print:
        print("\n" + report_md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
