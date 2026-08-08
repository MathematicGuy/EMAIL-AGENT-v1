"""Cowork Demo — Streamlit showcase frontend (SPEC-Demo-Frontend Increment A).

Five-screen information architecture on the proven 3-step spine:
Connect → Run (@Email) → Tasks (+ Task detail) → Run audit.

The module is import-safe: all Streamlit side effects live in ``main()``,
which only runs under ``streamlit run`` (``__name__ == "__main__"``).
Pure presentation helpers above ``main`` are unit-tested in
``tests/unit/gui/test_app.py``.
"""

import html
import math
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from dotenv import load_dotenv

SUPPORTED_LANGUAGES = ("vi", "en")

STRINGS: dict[str, dict[str, str]] = {
    "vi": {
        "app_title": "📧 Module Mail",
        "subtitle": "Biến email Gmail chưa đọc thành danh sách công việc tự động",
        "loading": "Đang tải…",
        "backend_down": "Chưa kết nối được với Server Backend (`mail-todo-api`).",
        "backend_down_help": (
            "Vui lòng bật server backend trong Terminal bằng lệnh:\n"
            "```powershell\n.\\.venv\\Scripts\\mail-todo-api.exe\n```"
        ),
        "step1_title": "Kết Nối Tài Khoản Gmail",
        "step2_title": "Chạy @Email — Quét & Phân Tích Task",
        "step3_title": "Danh Sách Công Việc",
        "audit_title": "Kiểm Toán Run",
        "connections_error": "Không thể tải danh sách kết nối (mã {code}).",
        "connections_empty": "Chưa có tài khoản Gmail nào được kết nối.",
        "connections_empty_hint": "Bấm nút bên dưới để kết nối tài khoản Gmail đầu tiên.",
        "connect_gmail": "🔑 Kết Nối Gmail Mới",
        "disconnect": "Ngắt kết nối",
        "disconnected_flash": "Đã ngắt kết nối {email}.",
        "disconnect_failed": "Ngắt kết nối thất bại (mã {code}).",
        "connection_picker": "Tài khoản Gmail:",
        "max_emails_slider": "Số lượng email chưa đọc tối đa cần quét:",
        "start_run": "🚀 BẮT ĐẦU QUÉT MAIL & TẠO DANH SÁCH TASK",
        "need_connection_hint": (
            "💡 Vui lòng hoàn thành Bước 1 (Kết nối Gmail) trước khi quét mail."
        ),
        "run_creating": "1. Gửi request tạo Digest Run…",
        "run_created": "2. Đã tạo Run ID: `{run_id}`. Đang quét email và gọi {provider}…",
        "run_progress": (
            "⏳ Tiến độ: Đã xử lý {processed}/{total} email "
            "(Gmail tìm thấy {matched})… (Trạng thái: `{status}`)"
        ),
        "run_success": "✅ Quét mail và phân tích công việc hoàn tất!",
        "run_failed_title": "❌ Quét thất bại [{code}]: {message}",
        "error_code_label": "Mã lỗi",
        "error_detail_label": "Chi tiết",
        "error_unknown": "Backend không cung cấp chi tiết lỗi.",
        "run_timeout": (
            "⚠️ Hết thời gian chờ sau {seconds} giây. Bấm nút bắt đầu lại để tiếp tục "
            "theo dõi cùng Run (Idempotency-Key được giữ nguyên, không tạo Run mới)."
        ),
        "run_create_failed": "❌ Tạo tiến trình quét thất bại (mã {code}).",
        "tasks_found": "🎉 Đã tìm thấy và trích xuất **{count} công việc** từ email chưa đọc!",
        "tasks_empty": "💌 Không có công việc nào mới trong các email chưa đọc vừa quét.",
        "tasks_none_yet": (
            "Chưa có kết quả nào. Bấm nút **'{button}'** ở Bước 2 để chạy!"
        ),
        "tasks_fetch_error": "Không thể tải danh sách task (mã {code}).",
        "deadline_label": "Hạn chót",
        "confidence_label": "Độ tin cậy phân loại",
        "route_label": "Luồng xử lý",
        "actionability_label": "Mức hành động",
        "correlated": "Tổng hợp từ {count} email",
        "partial_plan_badge": "⚠ KẾ HOẠCH CHƯA ĐỦ THÔNG TIN",
        "missing_info_title": "⚠️ Thiếu thông tin để hoàn tất kế hoạch:",
        "steps_expander": "📌 Các bước thực hiện chi tiết",
        "detail_picker": "Xem chi tiết công việc:",
        "detail_none": "Chọn một công việc phía trên để xem chi tiết.",
        "open_gmail": "↗️ Mở Email Trong Gmail",
        "supporting_docs": "Tài liệu tham chiếu (Citation)",
        "request_summary_label": "Yêu cầu",
        "validation_label": "Trạng thái xác thực",
        "unknown": "Không rõ",
        "audit_none": "Chưa có Run nào để kiểm toán. Kết quả sẽ hiện sau khi chạy @Email ở Bước 2.",
        "audit_status_label": "Trạng thái Run",
        "audit_progress_label": "Tiến độ",
        "audit_processed_expander": "📨 {count} email đã được xử lý trong run này",
        "audit_route_summary": "Tóm tắt phân loại (từ danh sách Task)",
        "audit_totals": "Tổng cộng {total} task, trong đó {partial} kế hoạch chưa đủ thông tin.",
        "audit_header_route": "Luồng xử lý",
        "audit_header_count": "Số task",
        "audit_retrieval": (
            "Tra cứu tài liệu: {tasks} task dùng tra cứu, {docs} tài liệu tham chiếu."
        ),
        "audit_validation": "Trạng thái xác thực: {summary}",
        "audit_telemetry_note": (
            "Ghi chú: độ trễ từng giai đoạn (stage latencies) chưa được backend này công bố; "
            "phần FR-16 đó thuộc milestone sau."
        ),
        "audit_fetch_error": "Không thể tải dữ liệu kiểm toán (mã {code}).",
        "sender_label": "Từ",
        "scan_mode_label": "Chế độ quét email:",
        "scan_mode_unread": "📨 Email chưa đọc (is:unread in:inbox)",
        "scan_mode_all": (
            "🔄 Quét lại Top K email mới nhất trong Inbox (Test mode - bao gồm email đã đọc)"
        ),
        "scan_ordering_hint": (
            "💡 Gmail trả về email mới nhất trước (ngày giảm dần). "
            "Hệ thống sẽ xử lý tối đa Top {max_emails} email mới nhất."
        ),
        "sort_label": "Sắp xếp theo:",
        "sort_priority_desc": "Mức ưu tiên: Cao ➔ Thấp",
        "sort_priority_asc": "Mức ưu tiên: Thấp ➔ Cao",
        "sort_deadline": "Hạn chót (Gần nhất)",
        "sort_default": "Thứ tự hệ thống",
        "filter_priority_label": "Lọc theo mức ưu tiên:",
        "filter_all": "Tất cả",
    },
    "en": {
        "app_title": "📧 Module Mail",
        "subtitle": "Turn unread Gmail into an automatic task list",
        "loading": "Loading…",
        "backend_down": "Cannot reach the backend server (`mail-todo-api`).",
        "backend_down_help": (
            "Please start the backend server in a terminal with:\n"
            "```powershell\n.\\.venv\\Scripts\\mail-todo-api.exe\n```"
        ),
        "step1_title": "Connect your Gmail account",
        "step2_title": "Run @Email — Scan & Extract Tasks",
        "step3_title": "Task List",
        "audit_title": "Run Audit",
        "connections_error": "Could not load the connection list (code {code}).",
        "connections_empty": "No Gmail accounts connected yet.",
        "connections_empty_hint": "Use the button below to connect your first Gmail account.",
        "connect_gmail": "🔑 Connect a new Gmail account",
        "disconnect": "Disconnect",
        "disconnected_flash": "Disconnected {email}.",
        "disconnect_failed": "Disconnect failed (code {code}).",
        "connection_picker": "Gmail account:",
        "max_emails_slider": "Maximum unread emails to scan:",
        "start_run": "🚀 START SCAN & CREATE TASK LIST",
        "need_connection_hint": "💡 Please complete Step 1 (Connect Gmail) before scanning.",
        "run_creating": "1. Sending the digest-run request…",
        "run_created": "2. Created Run ID: `{run_id}`. Scanning emails and calling {provider}…",
        "run_progress": (
            "⏳ Progress: processed {processed}/{total} emails "
            "(Gmail matched {matched})… (Status: `{status}`)"
        ),
        "run_success": "✅ Scan and task analysis complete!",
        "run_failed_title": "❌ Scan failed [{code}]: {message}",
        "error_code_label": "Error code",
        "error_detail_label": "Details",
        "error_unknown": "The backend did not provide error details.",
        "run_timeout": (
            "⚠️ Timed out after {seconds} seconds. Click start again to keep watching the "
            "same Run (the Idempotency-Key is reused, so no second Run is created)."
        ),
        "run_create_failed": "❌ Failed to create the scan run (code {code}).",
        "tasks_found": "🎉 Found and extracted **{count} tasks** from unread emails!",
        "tasks_empty": "💌 No new tasks in the unread emails just scanned.",
        "tasks_none_yet": "No results yet. Click **'{button}'** in Step 2 to run!",
        "tasks_fetch_error": "Could not load the task list (code {code}).",
        "deadline_label": "Deadline",
        "confidence_label": "Classifier confidence",
        "route_label": "Route",
        "actionability_label": "Actionability",
        "correlated": "Correlated from {count} emails",
        "partial_plan_badge": "⚠ PARTIAL PLAN",
        "missing_info_title": "⚠️ Missing information to complete the plan:",
        "steps_expander": "📌 Detailed action plan steps",
        "detail_picker": "View task detail:",
        "detail_none": "Select a task above to see its detail.",
        "open_gmail": "↗️ Open email in Gmail",
        "supporting_docs": "Supporting documents (Citations)",
        "request_summary_label": "Request",
        "validation_label": "Validation status",
        "unknown": "Unknown",
        "audit_none": "No Run to audit yet. Results appear after running @Email in Step 2.",
        "audit_status_label": "Run status",
        "audit_progress_label": "Progress",
        "audit_processed_expander": "📨 {count} emails processed in this run",
        "audit_route_summary": "Routing summary (from the Task list)",
        "audit_totals": "Total {total} tasks, of which {partial} are partial plans.",
        "audit_header_route": "Route",
        "audit_header_count": "Tasks",
        "audit_retrieval": "Retrieval: {tasks} tasks used retrieval, {docs} supporting documents.",
        "audit_validation": "Validation status: {summary}",
        "audit_telemetry_note": (
            "Note: stage latencies are not exposed by this backend yet; that part of "
            "FR-16 belongs to a later milestone."
        ),
        "audit_fetch_error": "Could not load audit data (code {code}).",
        "sender_label": "From",
        "scan_mode_label": "Scan mode:",
        "scan_mode_unread": "📨 Unread emails (is:unread in:inbox)",
        "scan_mode_all": (
            "🔄 Re-process Top K recent emails in Inbox (Test mode - includes read emails)"
        ),
        "scan_ordering_hint": (
            "💡 Gmail returns newest emails first (descending by date). "
            "The system processes top {max_emails} newest emails."
        ),
        "sort_label": "Sort by:",
        "sort_priority_desc": "Priority: High ➔ Low",
        "sort_priority_asc": "Priority: Low ➔ High",
        "sort_deadline": "Deadline (Soonest)",
        "sort_default": "Default order",
        "filter_priority_label": "Filter by priority:",
        "filter_all": "All",
    },
}

ENUM_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "route": {
        "direct_plan": {"vi": "Kế hoạch trực tiếp", "en": "Direct plan"},
        "retrieve_rag": {"vi": "Tra cứu tri thức", "en": "Knowledge retrieval"},
        "no_action": {"vi": "Không cần hành động", "en": "No action"},
    },
    "actionability": {
        "action_required": {"vi": "Cần hành động", "en": "Action required"},
        "action_suggested": {"vi": "Gợi ý hành động", "en": "Action suggested"},
        "informational": {"vi": "Thông tin", "en": "Informational"},
        "unclear": {"vi": "Chưa rõ", "en": "Unclear"},
        "irrelevant": {"vi": "Không liên quan", "en": "Irrelevant"},
    },
    "validation_status": {
        "system_generated": {"vi": "Hệ thống tạo", "en": "System generated"},
        "user_approved": {"vi": "Người dùng duyệt", "en": "User approved"},
        "completed": {"vi": "Hoàn thành", "en": "Completed"},
        "rejected": {"vi": "Từ chối", "en": "Rejected"},
    },
}

PRIORITY_LABELS: dict[str, dict[str, str]] = {
    "urgent": {"vi": "KHẨN CẤP", "en": "URGENT"},
    "high": {"vi": "CAO", "en": "HIGH"},
    "medium": {"vi": "TRUNG BÌNH", "en": "MEDIUM"},
    "low": {"vi": "THẤP", "en": "LOW"},
}

_GUI_CSS = """
<style>
.main-title {
    font-size: 2.2rem;
    font-weight: 800;
    text-align: center;
    color: #2563EB;
    margin-bottom: 0.2rem;
}
.subtitle {
    text-align: center;
    color: #4B5563;
    font-size: 1.05rem;
    margin-bottom: 2rem;
}
.step-box {
    background-color: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    padding: 1.25rem;
    margin-bottom: 1.5rem;
}
.step-number {
    display: inline-block;
    background: #2563EB;
    color: white;
    font-weight: 700;
    border-radius: 50%;
    width: 28px;
    height: 28px;
    text-align: center;
    line-height: 28px;
    margin-right: 8px;
}
.task-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-left: 5px solid #3B82F6;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.task-card.partial-plan {
    border-left: 5px dashed #D97706;
}
.priority-urgent { border-left-color: #EF4444 !important; }
.priority-high { border-left-color: #F97316 !important; }
.priority-medium { border-left-color: #F59E0B !important; }
.priority-low { border-left-color: #6B7280 !important; }
.priority-unknown { border-left-color: #94A3B8 !important; }

.badge {
    font-size: 0.75rem;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 4px;
    text-transform: uppercase;
}
.bg-urgent { background-color: #FEE2E2; color: #991B1B; }
.bg-high { background-color: #FFEDD5; color: #9A3412; }
.bg-medium { background-color: #FEF3C7; color: #92400E; }
.bg-low { background-color: #F3F4F6; color: #374151; }
.bg-unknown { background-color: #E2E8F0; color: #334155; }
.bg-route { background-color: #DBEAFE; color: #1E40AF; text-transform: none; }
.bg-action { background-color: #F3F4F6; color: #374151; text-transform: none; }
.bg-partial { background-color: #FEF3C7; color: #92400E; }
.citation-chip {
    display: inline-block;
    font-size: 0.75rem;
    font-weight: 600;
    color: #1D4ED8;
    background-color: #DBEAFE;
    border: 1px solid #BFDBFE;
    border-radius: 9999px;
    padding: 2px 10px;
    margin: 2px 4px 2px 0;
    text-decoration: none;
}
.citation-chip:hover { background-color: #BFDBFE; }
</style>
"""


def tr(lang: str, key: str, **fmt: object) -> str:
    """Translate ``key`` for ``lang`` and interpolate named placeholders."""
    text = STRINGS[lang][key]
    return text.format(**fmt) if fmt else text


def enum_label(kind: str, value: str | None, lang: str) -> str:
    if not value:
        return tr(lang, "unknown")
    return ENUM_LABELS.get(kind, {}).get(value, {}).get(lang, value)


def priority_key(priority: str | None) -> str:
    key = (priority or "").lower()
    return key if key in PRIORITY_LABELS else "unknown"


def priority_label(priority: str | None, lang: str) -> str:
    key = priority_key(priority)
    if key == "unknown":
        return tr(lang, "unknown").upper()
    return PRIORITY_LABELS[key].get(lang, key.upper())


PRIORITY_RANK: dict[str, int] = {
    "urgent": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "unknown": 0,
}


def filter_tasks(
    tasks: Sequence[Mapping[str, Any]], priority_filter: str = "all"
) -> list[dict[str, Any]]:
    """Filter tasks by priority key ('all', 'urgent', 'high', 'medium', 'low', 'unknown')."""
    p_filter = priority_filter.lower()
    if p_filter == "all":
        return [dict(t) for t in tasks]
    return [
        dict(t)
        for t in tasks
        if priority_key(str(t.get("priority")) if t.get("priority") else None) == p_filter
    ]


def sort_tasks(
    tasks: Sequence[Mapping[str, Any]], sort_key: str = "priority_desc"
) -> list[dict[str, Any]]:
    """Sort tasks deterministically by priority rank, deadline, or original order."""
    task_list = [dict(t) for t in tasks]
    if sort_key == "priority_desc":
        return sorted(
            task_list,
            key=lambda t: PRIORITY_RANK.get(
                priority_key(str(t.get("priority")) if t.get("priority") else None), 0
            ),
            reverse=True,
        )
    if sort_key == "priority_asc":
        return sorted(
            task_list,
            key=lambda t: PRIORITY_RANK.get(
                priority_key(str(t.get("priority")) if t.get("priority") else None), 0
            ),
        )
    if sort_key == "deadline":

        def _deadline_key(t: Mapping[str, Any]) -> str:
            d = t.get("deadline")
            return str(d) if d else "9999-99-99"

        return sorted(task_list, key=_deadline_key)
    return task_list


def format_deadline(deadline: object, lang: str) -> str:
    """Render an ISO deadline as a compact human string (metadata only)."""
    if not deadline:
        return tr(lang, "unknown")
    text = str(deadline).replace("T", " ")
    return text[:16] if len(text) > 16 else text


def format_confidence(confidence: object) -> str:
    if isinstance(confidence, (int, float)):
        return f"{float(confidence):.0%}"
    return "?"


def safe_url(url: object) -> str | None:
    """Return an http(s) URL safe for href interpolation, else None."""
    text = str(url or "")
    return text if text.startswith(("http://", "https://")) else None


def citation_chip_html(title: object, url: object) -> str:
    """One citation chip; LLM-sourced text is escaped, hrefs scheme-limited."""
    label = html.escape(str(title or "").strip() or "…", quote=True)
    href = safe_url(url)
    if href is None:
        return f'<span class="citation-chip">📎 {label}</span>'
    escaped = html.escape(href, quote=True)
    return (
        f'<a class="citation-chip" href="{escaped}"'
        f' target="_blank" rel="noopener">📎 {label}</a>'
    )


def _as_list(value: object) -> list[Any]:
    """Non-string Sequence extraction; a bare str would iterate characters."""
    return list(value) if isinstance(value, Sequence) and not isinstance(value, str) else []


def task_documents(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(doc) for doc in _as_list(task.get("supporting_documents"))]


def task_missing_information(task: Mapping[str, Any]) -> list[str]:
    return [str(gap) for gap in _as_list(task.get("missing_information"))]


def task_source_count(task: Mapping[str, Any]) -> int:
    ids = _as_list(task.get("source_message_ids"))
    return len(ids) or 1


def is_partial_plan(task: Mapping[str, Any]) -> bool:
    return bool(task_missing_information(task))


def build_task_card_html(task: Mapping[str, Any], index: int, lang: str) -> str:
    """Safe HTML for one Task card (SPEC §6.2: color always paired with text)."""
    title = html.escape(str(task.get("title") or tr(lang, "unknown")), quote=True)
    summary = html.escape(str(task.get("request_summary") or ""), quote=True)
    p_key = priority_key(str(task.get("priority")) if task.get("priority") else None)
    priority_text = html.escape(priority_label(str(task.get("priority")), lang), quote=True)
    partial_class = " partial-plan" if is_partial_plan(task) else ""
    route_text = html.escape(enum_label("route", str(task.get("route")), lang), quote=True)
    action_text = html.escape(
        enum_label("actionability", str(task.get("actionability")), lang), quote=True
    )
    badges = (
        f'<span class="badge bg-route">{route_text}</span>'
        f'<span class="badge bg-action">{action_text}</span>'
    )
    if is_partial_plan(task):
        badges += f'<span class="badge bg-partial">{tr(lang, "partial_plan_badge")}</span>'
    correlated = ""
    source_count = task_source_count(task)
    if source_count > 1:
        correlated = f" · {tr(lang, 'correlated', count=source_count)}"
    deadline = html.escape(format_deadline(task.get("deadline"), lang), quote=True)
    confidence = format_confidence(task.get("classifier_confidence"))
    head_style = (
        'style="display: flex; justify-content: space-between;'
        ' align-items: center; margin-bottom: 8px;"'
    )
    return (
        f'<div class="task-card priority-{p_key}{partial_class}">'
        f"<div {head_style}>"
        f'<strong style="font-size: 1.1rem; color: #1E293B;">{index}. {title}</strong>'
        f'<span class="badge bg-{p_key}">{priority_text}</span>'
        f"</div>"
        f'<p style="color: #475569; font-size: 0.95rem; margin-bottom: 8px;">{summary}</p>'
        f'<div style="display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px;">{badges}</div>'
        f'<div style="font-size: 0.85rem; color: #64748B;">'
        f"<strong>{tr(lang, 'deadline_label')}:</strong> <code>{deadline}</code> | "
        f"<strong>{tr(lang, 'confidence_label')}:</strong> "
        f"<code>{confidence}</code>{correlated}"
        f"</div></div>"
    )


def route_summary(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Routing/retrieval/validation summary for the Run audit screen."""
    routes: dict[str, int] = {}
    validations: dict[str, int] = {}
    retrieval_tasks = 0
    document_count = 0
    partial_plans = 0
    for task in tasks:
        route = str(task.get("route") or "unknown")
        routes[route] = routes.get(route, 0) + 1
        validation = str(task.get("validation_status") or "unknown")
        validations[validation] = validations.get(validation, 0) + 1
        if route == "retrieve_rag":
            retrieval_tasks += 1
            document_count += len(task_documents(task))
        if is_partial_plan(task):
            partial_plans += 1
    return {
        "routes": routes,
        "validations": validations,
        "retrieval_tasks": retrieval_tasks,
        "document_count": document_count,
        "partial_plans": partial_plans,
        "total": len(tasks),
    }


def _get_http_client() -> httpx.Client:
    """Return a shared httpx.Client instance cached via Streamlit if available."""
    try:
        import streamlit as st

        @st.cache_resource
        def _cached_client() -> httpx.Client:
            return httpx.Client(timeout=15.0)

        return _cached_client()
    except Exception:
        return httpx.Client(timeout=15.0)


def api_request(base_url: str, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        client = _get_http_client()
        res = client.request(method, url, **kwargs)
        try:
            return res.status_code, res.json()
        except Exception:
            return res.status_code, res.text
    except Exception as e:
        return 0, str(e)


def _check_health(base_url: str) -> tuple[int, Any]:
    try:
        import streamlit as st

        @st.cache_data(ttl=5.0, show_spinner=False)
        def _cached_health(url: str) -> tuple[int, Any]:
            return api_request(url, "GET", "/health")

        return _cached_health(base_url)
    except Exception:
        return api_request(base_url, "GET", "/health")


def read_settings() -> dict[str, Any]:
    llm_provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if llm_provider == "groq":
        batch = max(1, int(os.getenv("GROQ_MAX_EMAILS_PER_BATCH", "5")))
        timeout = max(1, int(os.getenv("GROQ_TIMEOUT_SECONDS", "60")))
        provider_name = "Groq"
    else:
        batch = max(1, int(os.getenv("GEMINI_MAX_EMAILS_PER_BATCH", "5")))
        timeout = max(1, int(os.getenv("GEMINI_TIMEOUT_SECONDS", "60")))
        provider_name = "Gemini"
    return {
        "api_base_url": os.getenv("APP_HOST_URL", "http://localhost:8000"),
        "llm_provider": llm_provider,
        "provider_name": provider_name,
        "batch_size": batch,
        "llm_timeout_seconds": timeout,
        "poll_interval_seconds": max(
            0.5, float(os.getenv("STREAMLIT_POLL_INTERVAL_SECONDS", "1.5"))
        ),
        "poll_buffer_seconds": max(0, int(os.getenv("STREAMLIT_POLL_BUFFER_SECONDS", "30"))),
    }


# -----------------------------------------------------------------------------
# Streamlit UI — everything below requires a Streamlit runtime.
# -----------------------------------------------------------------------------


def main() -> None:
    import streamlit as st

    start_time = time.perf_counter()
    load_dotenv()
    settings = read_settings()
    base_url: str = settings["api_base_url"]

    st.set_page_config(
        page_title="Module Mail — Cowork Demo",
        page_icon="📧",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    st.markdown(_GUI_CSS, unsafe_allow_html=True)

    col_title, col_lang = st.columns([4, 1])
    with col_lang:
        lang = str(
            st.selectbox(
                "🌐 Language / Ngôn ngữ", options=list(SUPPORTED_LANGUAGES), index=0,
                format_func=str.upper, key="demo_language",
            )
        )

    with col_title:
        st.markdown(
            f'<div class="main-title">{tr(lang, "app_title")}</div>', unsafe_allow_html=True
        )
        st.markdown(f'<div class="subtitle">{tr(lang, "subtitle")}</div>', unsafe_allow_html=True)

    flash = st.session_state.pop("flash", None)
    if isinstance(flash, dict) and flash.get("message"):
        level = flash.get("level")
        message = str(flash["message"])
        if level == "error":
            st.error(message)
        elif level == "warning":
            st.warning(message)
        else:
            st.success(message)

    health_code, _ = _check_health(base_url)
    if health_code != 200:
        st.error(f"⚠️ {tr(lang, 'backend_down')}")
        st.info(tr(lang, "backend_down_help"))
        st.stop()

    connections = _load_connections(base_url, lang)

    _screen_connect(base_url, lang, connections)
    st.markdown("---")
    _screen_run(base_url, lang, settings, connections)
    st.markdown("---")
    _screen_tasks(base_url, lang)
    st.markdown("---")
    _screen_audit(base_url, lang)

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    st.caption(f"⚡ *Streamlit Rerun Latency: {elapsed_ms:.1f} ms*")


def _load_connections(base_url: str, lang: str) -> list[dict[str, Any]]:
    import streamlit as st

    try:
        @st.cache_data(ttl=10.0, show_spinner=False)
        def _cached_connections(url: str) -> tuple[int, Any]:
            return api_request(url, "GET", "/v1/mail-todo/connections")

        code, res = _cached_connections(base_url)
    except Exception:
        code, res = api_request(base_url, "GET", "/v1/mail-todo/connections")

    if code == 200 and isinstance(res, dict):
        return [dict(item) for item in _as_list(res.get("connections"))]
    st.error(tr(lang, "connections_error", code=code))
    return []


def _screen_connect(base_url: str, lang: str, connections: list[dict[str, Any]]) -> None:
    import streamlit as st

    st.markdown(
        f'### <span class="step-number">1</span> {tr(lang, "step1_title")}',
        unsafe_allow_html=True,
    )
    col_list, col_action = st.columns([2, 1])
    with col_action:
        connect_url = f"{base_url.rstrip('/')}/v1/mail-todo/oauth/gmail/connect"
        st.link_button(
            tr(lang, "connect_gmail"), connect_url,
            type="secondary" if connections else "primary",
            use_container_width=True,
        )
    with col_list:
        if not connections:
            st.warning(tr(lang, "connections_empty"))
            st.caption(tr(lang, "connections_empty_hint"))
            return
        for connection in connections:
            email = str(connection.get("emailAddress") or tr(lang, "unknown"))
            status = str(connection.get("status") or tr(lang, "unknown"))
            row_left, row_right = st.columns([3, 1])
            row_left.markdown(
                f"📫 **{html.escape(email)}** · `{html.escape(status)}`"
            )
            if row_right.button(
                tr(lang, "disconnect"), key=f"disconnect-{connection.get('id')}",
                use_container_width=True,
            ):
                code, _ = api_request(
                    base_url, "DELETE", f"/v1/mail-todo/connections/{connection.get('id')}"
                )
                if code == 200:
                    try:
                        st.cache_data.clear()
                    except Exception:
                        pass
                    st.session_state["flash"] = {
                        "message": tr(lang, "disconnected_flash", email=email),
                        "level": "success",
                    }
                else:
                    st.session_state["flash"] = {
                        "message": tr(lang, "disconnect_failed", code=code),
                        "level": "error",
                    }
                st.rerun()


def _screen_run(
    base_url: str, lang: str, settings: dict[str, Any], connections: list[dict[str, Any]]
) -> None:
    import streamlit as st

    def _render_run_body() -> None:
        st.markdown(
            f'### <span class="step-number">2</span> {tr(lang, "step2_title")}',
            unsafe_allow_html=True,
        )
        selected_connection_id: str | None = None
        if connections:
            options = {
                str(c.get("id")): f"📫 {c.get('emailAddress')} ({c.get('status')})"
                for c in connections
            }
            selected_connection_id = st.selectbox(
                tr(lang, "connection_picker"),
                options=list(options.keys()),
                format_func=lambda x: options[x],
            )
        else:
            st.warning(tr(lang, "connections_empty"))

        scan_mode = st.radio(
            tr(lang, "scan_mode_label"),
            options=["unread", "all"],
            format_func=lambda x: tr(lang, f"scan_mode_{x}"),
            horizontal=True,
        )
        selected_query = "is:unread in:inbox" if scan_mode == "unread" else "in:inbox"

        max_emails = st.slider(
            tr(lang, "max_emails_slider"), min_value=5, max_value=100, value=20, step=5
        )
        st.caption(tr(lang, "scan_ordering_hint", max_emails=max_emails))

        start_clicked = st.button(
            tr(lang, "start_run"), type="primary", use_container_width=True,
            disabled=not selected_connection_id,
        )
        if not selected_connection_id:
            st.info(tr(lang, "need_connection_hint"))
            return

        if start_clicked:
            _create_and_watch_run(
                base_url, lang, settings, selected_connection_id, selected_query, int(max_emails)
            )

    if hasattr(st, "fragment"):
        @st.fragment
        def _run_fragment() -> None:
            _render_run_body()

        _run_fragment()
    else:
        _render_run_body()


def _create_and_watch_run(
    base_url: str,
    lang: str,
    settings: dict[str, Any],
    connection_id: str,
    query: str,
    max_emails: int,
) -> None:
    """Idempotent Run creation (SPEC §6.7/§8.5) + bounded status polling."""
    import streamlit as st

    # A retry click on the same form reuses the stored Idempotency-Key, so the
    # backend returns the existing Run instead of creating a second one.
    active = st.session_state.get("active_run")
    active_run = dict(active) if isinstance(active, dict) else None
    if active_run is not None and not (
        active_run.get("connection_id") == connection_id
        and active_run.get("query") == query
        and active_run.get("max_emails") == max_emails
    ):
        active_run = None
    if active_run is not None:
        idempotency_key = str(active_run["key"])
        run_id: str | None = str(active_run["run_id"]) if active_run.get("run_id") else None
    else:
        idempotency_key = str(uuid.uuid4())
        run_id = None
    with st.status(tr(lang, "run_creating"), expanded=True) as status:
        if run_id is None:
            code, res = api_request(
                base_url, "POST", "/v1/mail-todo/runs",
                headers={"Idempotency-Key": idempotency_key, "Content-Type": "application/json"},
                json={
                    "mailboxConnectionId": connection_id,
                    "query": query,
                    "maxEmails": max_emails,
                },
            )
            created_id = res.get("id") if isinstance(res, dict) else None
            if code != 202 or not created_id:
                status.update(label=tr(lang, "run_create_failed", code=code), state="error")
                _render_error(lang, res)
                return
            run_id = str(created_id)
        st.session_state["active_run"] = {
            "run_id": run_id,
            "key": idempotency_key,
            "connection_id": connection_id,
            "query": query,
            "max_emails": max_emails,
        }
        st.write(tr(lang, "run_created", run_id=run_id, provider=settings["provider_name"]))
        _poll_run(base_url, lang, settings, status, run_id, max_emails)


def _poll_run(
    base_url: str,
    lang: str,
    settings: dict[str, Any],
    status: Any,
    run_id: str,
    max_emails: int,
) -> None:
    import streamlit as st

    batch_count = max(1, math.ceil(max_emails / settings["batch_size"]))
    poll_timeout_seconds = (
        batch_count * settings["llm_timeout_seconds"] + settings["poll_buffer_seconds"]
    )
    poll_deadline = time.monotonic() + poll_timeout_seconds
    progress_line = st.empty()
    while time.monotonic() < poll_deadline:
        time.sleep(settings["poll_interval_seconds"])
        s_code, s_res = api_request(base_url, "GET", f"/v1/mail-todo/runs/{run_id}")
        if s_code != 200 or not isinstance(s_res, dict):
            continue
        current_status = s_res.get("status")
        raw_progress = s_res.get("progress")
        progress = dict(raw_progress) if isinstance(raw_progress, dict) else {}
        progress_line.write(
            tr(
                lang, "run_progress",
                processed=progress.get("emailsProcessed", 0),
                total=progress.get("emailsToProcess", max_emails),
                matched=progress.get("emailsMatched", 0),
                status=current_status,
            )
        )
        if current_status in {"succeeded", "partial"}:
            status.update(label=tr(lang, "run_success"), state="complete", expanded=False)
            st.session_state["last_run_id"] = run_id
            st.session_state.pop("active_run", None)
            st.rerun()
            return
        if current_status == "failed":
            raw_error = s_res.get("error")
            err = dict(raw_error) if isinstance(raw_error, dict) else {}
            error_code = str(err.get("code") or "UNKNOWN_ERROR")
            error_message = str(err.get("message") or tr(lang, "error_unknown"))
            status.update(
                label=tr(lang, "run_failed_title", code=error_code, message=error_message),
                state="error",
            )
            st.error(
                f"**{tr(lang, 'error_code_label')}:** `{error_code}`  \n"
                f"**{tr(lang, 'error_detail_label')}:** {error_message}"
            )
            st.session_state.pop("active_run", None)
            return
    status.update(label=tr(lang, "run_timeout", seconds=poll_timeout_seconds), state="error")


def _render_error(lang: str, res: Any) -> None:
    import streamlit as st

    detail = (res.get("detail") or res) if isinstance(res, dict) else res
    st.error(f"**{tr(lang, 'error_detail_label')}:** {detail}")


def _fetch_tasks(base_url: str, run_id: str) -> tuple[int, list[dict[str, Any]]]:
    try:
        import streamlit as st

        @st.cache_data(ttl=300.0, show_spinner=False)
        def _cached_fetch(url: str, r_id: str) -> tuple[int, list[dict[str, Any]]]:
            code, res = api_request(url, "GET", f"/v1/mail-todo/runs/{r_id}/tasks")
            if code == 200 and isinstance(res, dict):
                return code, [dict(item) for item in _as_list(res.get("tasks"))]
            return code, []

        return _cached_fetch(base_url, run_id)
    except Exception:
        code, res = api_request(base_url, "GET", f"/v1/mail-todo/runs/{run_id}/tasks")
        if code == 200 and isinstance(res, dict):
            return code, [dict(item) for item in _as_list(res.get("tasks"))]
        return code, []


def _fetch_run_audit(base_url: str, run_id: str) -> tuple[int, Any]:
    try:
        import streamlit as st

        @st.cache_data(ttl=5.0, show_spinner=False)
        def _cached_audit(url: str, r_id: str) -> tuple[int, Any]:
            return api_request(url, "GET", f"/v1/mail-todo/runs/{r_id}")

        return _cached_audit(base_url, run_id)
    except Exception:
        return api_request(base_url, "GET", f"/v1/mail-todo/runs/{run_id}")


def _task_steps(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(step) for step in _as_list(task.get("action_plan"))]


def _render_plan_steps(lang: str, task: Mapping[str, Any]) -> None:
    """Ordered Action Plan with per-step Citation chips (SPEC §8.2)."""
    import streamlit as st

    citations_by_id = {str(doc.get("citation_id")): doc for doc in task_documents(task)}
    for step_index, step in enumerate(_task_steps(task), 1):
        instruction = html.escape(str(step.get("instruction") or ""))
        cited_ids = [str(cid) for cid in _as_list(step.get("supporting_citation_ids"))]
        chips = "".join(
            citation_chip_html(doc.get("title"), doc.get("url"))
            for cid in cited_ids
            if (doc := citations_by_id.get(cid)) is not None
        )
        st.markdown(f"**{step.get('step') or step_index}.** {instruction}")
        if chips:
            st.markdown(chips, unsafe_allow_html=True)


def _gmail_pointer_html(task: Mapping[str, Any], lang: str) -> str:
    """Safe anchor for the Gmail deep-link pointer (SPEC §8.2/§8.4)."""
    href = safe_url(task.get("gmail_url"))
    if href is None:
        return ""
    label = html.escape(tr(lang, "open_gmail"), quote=True)
    return (
        f'<a href="{html.escape(href, quote=True)}" target="_blank"'
        f' rel="noopener">{label}</a>'
    )


def _screen_tasks(base_url: str, lang: str) -> None:
    import streamlit as st

    def _render_content() -> None:
        st.markdown(
            f'### <span class="step-number">3</span> {tr(lang, "step3_title")}',
            unsafe_allow_html=True,
        )
        run_id = st.session_state.get("last_run_id")
        if not run_id:
            st.info(tr(lang, "tasks_none_yet", button=tr(lang, "start_run").removeprefix("🚀 ")))
            return

        code, tasks = _fetch_tasks(base_url, str(run_id))
        if code != 200:
            st.error(tr(lang, "tasks_fetch_error", code=code))
            return
        if not tasks:
            st.info(tr(lang, "tasks_empty"))
            return

        st.success(tr(lang, "tasks_found", count=len(tasks)))

        col_sort, col_filter = st.columns(2)
        with col_sort:
            sort_key = st.selectbox(
                tr(lang, "sort_label"),
                options=["priority_desc", "priority_asc", "deadline", "default"],
                format_func=lambda x: tr(lang, f"sort_{x}"),
                key="task_sort_select",
            )
        with col_filter:
            filter_key = st.selectbox(
                tr(lang, "filter_priority_label"),
                options=["all", "urgent", "high", "medium", "low"],
                format_func=lambda x: (
                    tr(lang, "filter_all") if x == "all" else priority_label(x, lang)
                ),
                key="task_filter_select",
            )

        filtered = filter_tasks(tasks, str(filter_key))
        display_tasks = sort_tasks(filtered, str(sort_key))

        for index, task in enumerate(display_tasks, 1):
            st.markdown(build_task_card_html(task, index, lang), unsafe_allow_html=True)
            missing = task_missing_information(task)
            if missing:
                st.warning(f"{tr(lang, 'missing_info_title')} {'; '.join(missing)}")
            pointer = _gmail_pointer_html(task, lang)
            if pointer:
                st.markdown(pointer, unsafe_allow_html=True)
            if _task_steps(task):
                with st.expander(tr(lang, "steps_expander"), key=f"steps-{task.get('task_id')}"):
                    _render_plan_steps(lang, task)

        _screen_task_detail(base_url, lang, display_tasks)

    if hasattr(st, "fragment"):
        @st.fragment
        def _tasks_fragment() -> None:
            _render_content()

        _tasks_fragment()
    else:
        _render_content()


def _screen_task_detail(base_url: str, lang: str, tasks: list[dict[str, Any]]) -> None:
    import streamlit as st

    options = {
        str(task.get("task_id") or index): f"{index}. {task.get('title') or tr(lang, 'unknown')}"
        for index, task in enumerate(tasks, 1)
    }
    selected = st.selectbox(
        tr(lang, "detail_picker"), options=list(options.keys()),
        format_func=lambda x: options[x],
    )
    task = next((t for t in tasks if str(t.get("task_id")) == selected), None)
    if task is None:
        st.caption(tr(lang, "detail_none"))
        return

    st.markdown(f"#### {html.escape(str(task.get('title') or tr(lang, 'unknown')))}")
    summary = str(task.get("request_summary") or "")
    if summary:
        st.markdown(f"**{tr(lang, 'request_summary_label')}:** {html.escape(summary)}")

    if _task_steps(task):
        _render_plan_steps(lang, task)

    documents = task_documents(task)
    if documents:
        st.markdown(f"**{tr(lang, 'supporting_docs')}:**")
        st.markdown(
            "".join(
                citation_chip_html(doc.get("title"), doc.get("url")) for doc in documents
            ),
            unsafe_allow_html=True,
        )

    missing = task_missing_information(task)
    if missing:
        st.warning(f"{tr(lang, 'missing_info_title')} {'; '.join(missing)}")

    meta_bits = [
        f"**{tr(lang, 'deadline_label')}:** `{format_deadline(task.get('deadline'), lang)}`",
        f"**{tr(lang, 'confidence_label')}:** "
        f"`{format_confidence(task.get('classifier_confidence'))}`",
        f"**{tr(lang, 'validation_label')}:** "
        f"{enum_label('validation_status', str(task.get('validation_status')), lang)}",
    ]
    source_count = task_source_count(task)
    if source_count > 1:
        meta_bits.append(f"**{tr(lang, 'correlated', count=source_count)}**")
    st.caption(" · ".join(meta_bits))

    gmail_url = safe_url(task.get("gmail_url"))
    if gmail_url:
        st.link_button(tr(lang, "open_gmail"), gmail_url)


def _screen_audit(base_url: str, lang: str) -> None:
    import streamlit as st

    def _render_audit_body() -> None:
        st.markdown(f"### 🔎 {tr(lang, 'audit_title')}", unsafe_allow_html=True)
        run_id = st.session_state.get("last_run_id")
        if not run_id:
            st.info(tr(lang, "audit_none"))
            return

        run_code, run_res = _fetch_run_audit(base_url, str(run_id))
        tasks_code, tasks = _fetch_tasks(base_url, str(run_id))
        if run_code != 200 or not isinstance(run_res, dict):
            st.error(tr(lang, "audit_fetch_error", code=run_code))
            return

        col_status, col_progress = st.columns(2)
        status_value = str(run_res.get("status") or tr(lang, "unknown"))
        col_status.metric(tr(lang, "audit_status_label"), status_value)
        raw_progress = run_res.get("progress")
        progress = dict(raw_progress) if isinstance(raw_progress, dict) else {}
        col_progress.metric(
            tr(lang, "audit_progress_label"),
            f"{progress.get('emailsProcessed', 0)}/{progress.get('emailsToProcess', 0)}",
        )

        error = run_res.get("error")
        if isinstance(error, dict) and error.get("code"):
            st.error(
                f"**{tr(lang, 'error_code_label')}:** `{error.get('code')}`  \n"
                f"**{tr(lang, 'error_detail_label')}:** "
                f"{error.get('message') or tr(lang, 'error_unknown')}"
            )

        processed = _as_list(run_res.get("processedEmails"))
        if processed:
            with st.expander(tr(lang, "audit_processed_expander", count=len(processed))):
                for index, item in enumerate(processed, 1):
                    subject = html.escape(str(item.get("subject") or tr(lang, "unknown")))
                    sender = html.escape(str(item.get("sender") or tr(lang, "unknown")))
                    st.markdown(
                        f"**{index}. {subject}**  \n{tr(lang, 'sender_label')}: `{sender}`"
                    )

        if tasks_code == 200 and tasks:
            summary = route_summary(tasks)
            st.markdown(f"**{tr(lang, 'audit_route_summary')}**")
            st.caption(
                tr(lang, "audit_totals", total=summary["total"], partial=summary["partial_plans"])
            )
            route_rows = [
                {
                    tr(lang, "audit_header_route"): enum_label("route", route, lang),
                    tr(lang, "audit_header_count"): count,
                }
                for route, count in sorted(summary["routes"].items())
            ]
            st.table(route_rows)
            st.caption(
                tr(
                    lang, "audit_retrieval",
                    tasks=summary["retrieval_tasks"], docs=summary["document_count"],
                )
            )
            validation_summary = ", ".join(
                f"{enum_label('validation_status', key, lang)}: {count}"
                for key, count in sorted(summary["validations"].items())
            )
            st.caption(tr(lang, "audit_validation", summary=validation_summary))
        elif tasks_code != 200:
            st.error(tr(lang, "tasks_fetch_error", code=tasks_code))

        st.caption(tr(lang, "audit_telemetry_note"))

    if hasattr(st, "fragment"):
        @st.fragment
        def _audit_fragment() -> None:
            _render_audit_body()

        _audit_fragment()
    else:
        _render_audit_body()


if __name__ == "__main__":
    main()
