"""Module Mail — Simple Streamlit Interface.

Streamlined 3-step UI:
1. Connect Gmail OAuth
2. Click one button to scan unread emails
3. View extracted tasks immediately
"""

import html
import math
import os
import time
import uuid
from typing import Any

import httpx
import streamlit as st
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Streamlit Page Config
st.set_page_config(
    page_title="Module Mail — Gmail Task Extractor",
    page_icon="📧",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Custom CSS for Minimalist Premium Aesthetics
st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 800;
        text-align: center;
        background: linear-gradient(135deg, #2563EB 0%, #7C3AED 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
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
    .priority-urgent { border-left-color: #EF4444 !important; }
    .priority-high { border-left-color: #F97316 !important; }
    .priority-medium { border-left-color: #F59E0B !important; }
    .priority-low { border-left-color: #6B7280 !important; }
    
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
    """,
    unsafe_allow_html=True,
)

# App Header
st.markdown('<div class="main-title">📧 Module Mail</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Biến Email Gmail Chưa Đọc Thành Danh Sách Công Việc Tự Động</div>',
    unsafe_allow_html=True,
)

# Configuration & Defaults
API_BASE_URL = os.getenv("APP_HOST_URL", "http://localhost:8000")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
if LLM_PROVIDER == "groq":
    LLM_MAX_EMAILS_PER_BATCH = max(1, int(os.getenv("GROQ_MAX_EMAILS_PER_BATCH", "5")))
    LLM_TIMEOUT_SECONDS = max(1, int(os.getenv("GROQ_TIMEOUT_SECONDS", "60")))
else:
    LLM_MAX_EMAILS_PER_BATCH = max(1, int(os.getenv("GEMINI_MAX_EMAILS_PER_BATCH", "5")))
    LLM_TIMEOUT_SECONDS = max(1, int(os.getenv("GEMINI_TIMEOUT_SECONDS", "60")))
POLL_INTERVAL_SECONDS = max(0.5, float(os.getenv("STREAMLIT_POLL_INTERVAL_SECONDS", "1.5")))
POLL_BUFFER_SECONDS = max(0, int(os.getenv("STREAMLIT_POLL_BUFFER_SECONDS", "30")))


def api_request(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    url = f"{API_BASE_URL.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.request(method, url, **kwargs)
            try:
                return res.status_code, res.json()
            except Exception:
                return res.status_code, res.text
    except Exception as e:
        return 0, str(e)


# Check API Health
health_code, health_res = api_request("GET", "/health")
if health_code != 200:
    st.error("⚠️ Chưa kết nối được với Server Backend (`mail-todo-api`).")
    st.info(
        "Vui lòng bật server backend trong Terminal bằng lệnh:\n"
        "```powershell\n.\\.venv\\Scripts\\mail-todo-api.exe\n```"
    )
    st.stop()

# -----------------------------------------------------------------------------
# STEP 1: Kết nối OAuth / Chọn Tài Khoản Gmail
# -----------------------------------------------------------------------------
st.markdown(
    '### <span class="step-number">1</span> Kết Nối Tài Khoản Gmail', unsafe_allow_html=True
)

conn_code, conn_res = api_request("GET", "/v1/mail-todo/connections")
connections = (
    conn_res.get("connections", []) if conn_code == 200 and isinstance(conn_res, dict) else []
)

selected_connection_id = None

col1, col2 = st.columns([2, 1])

with col1:
    if connections:
        conn_options = {
            c.get("id"): f"📫 {c.get('emailAddress')} ({c.get('status')})" for c in connections
        }
        selected_connection_id = st.selectbox(
            "Tài khoản Gmail đã kết nối:",
            options=list(conn_options.keys()),
            format_func=lambda x: conn_options[x],
            label_visibility="collapsed",
        )
    else:
        st.warning("Chưa có tài khoản Gmail nào được kết nối.")

with col2:
    connect_url = f"{API_BASE_URL.rstrip('/')}/v1/mail-todo/oauth/gmail/connect"
    st.link_button(
        "🔑 Kết Nối Gmail Mới",
        connect_url,
        type="secondary" if connections else "primary",
        use_container_width=True,
    )

st.markdown("---")

# -----------------------------------------------------------------------------
# STEP 2: Nút Bấm Quét Mail Chưa Đọc
# -----------------------------------------------------------------------------
st.markdown(
    '### <span class="step-number">2</span> Quét Email Chưa Đọc & Phân Tích Task',
    unsafe_allow_html=True,
)

max_emails = st.slider(
    "Số lượng email chưa đọc tối đa cần quét:", min_value=5, max_value=100, value=20, step=5
)

scan_clicked = st.button(
    "🚀 BẮT ĐẦU QUÉT MAIL & TẠO DANH SÁCH TASK",
    type="primary",
    use_container_width=True,
    disabled=not selected_connection_id,
)

if not selected_connection_id:
    st.info("💡 Vui lòng hoàn thành Bước 1 (Kết nối Gmail) trước khi quét mail.")

# Handle Scan Execution
if scan_clicked and selected_connection_id:
    with st.status("🔄 Đang xử lý email chưa đọc...", expanded=True) as status:
        st.write("1. Gửi request tạo Digest Run...")
        headers = {"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"}
        payload = {
            "mailboxConnectionId": selected_connection_id,
            "query": "is:unread in:inbox",
            "maxEmails": max_emails,
        }

        run_code, run_res = api_request(
            "POST",
            "/v1/mail-todo/runs",
            headers=headers,
            json=payload,
        )

        if run_code != 202 or not isinstance(run_res, dict):
            status.update(label="❌ Tạo tiến trình quét thất bại!", state="error")
            st.error(f"Lỗi khi gửi yêu cầu: {run_res}")
            st.stop()

        run_id = run_res.get("id")
        provider_name = "Groq Qwen" if LLM_PROVIDER == "groq" else "Gemini AI"
        st.write(f"2. Đã tạo Run ID: `{run_id}`. Đang quét email và gọi {provider_name}...")

        # Poll status
        completed = False
        processed_emails = []
        batch_count = max(1, math.ceil(max_emails / LLM_MAX_EMAILS_PER_BATCH))
        poll_timeout_seconds = batch_count * LLM_TIMEOUT_SECONDS + POLL_BUFFER_SECONDS
        poll_deadline = time.monotonic() + poll_timeout_seconds
        progress_line = st.empty()
        while time.monotonic() < poll_deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            s_code, s_res = api_request("GET", f"/v1/mail-todo/runs/{run_id}")
            if s_code == 200 and isinstance(s_res, dict):
                current_status = s_res.get("status")
                progress = s_res.get("progress", {})
                processed = progress.get("emailsProcessed", 0)
                to_process = progress.get("emailsToProcess", max_emails)
                matched = progress.get("emailsMatched", 0)
                processed_emails = s_res.get("processedEmails", [])

                progress_line.write(
                    f"⏳ Tiến độ: Đã xử lý {processed}/{to_process} email "
                    f"(Gmail tìm thấy {matched})... (Trạng thái: `{current_status}`)"
                )

                if current_status in {"succeeded", "partial"}:
                    completed = True
                    break
                elif current_status == "failed":
                    err = s_res.get("error") or {}
                    error_code = err.get("code") or "UNKNOWN_ERROR"
                    error_message = err.get("message") or "Backend không cung cấp chi tiết lỗi."
                    status.update(
                        label=f"❌ Quét thất bại [{error_code}]: {error_message}",
                        state="error",
                    )
                    st.error(
                        f"**Mã lỗi:** `{error_code}`  \n"
                        f"**Chi tiết:** {error_message}"
                    )
                    st.stop()

        if completed:
            status.update(
                label="✅ Quét mail và phân tích công việc hoàn tất!",
                state="complete",
                expanded=False,
            )
            st.session_state["last_run_id"] = run_id
            st.session_state["last_processed_emails"] = processed_emails
        else:
            status.update(
                label=(
                    f"⚠️ Hết thời gian chờ sau {poll_timeout_seconds} giây. "
                    "Vui lòng thử lại sau."
                ),
                state="error",
            )
            st.stop()

audit_emails = st.session_state.get("last_processed_emails", [])
if audit_emails:
    with st.expander(
        f"📨 {len(audit_emails)} email đã được xử lý trong run gần nhất",
        expanded=True,
    ):
        for index, email_item in enumerate(audit_emails, 1):
            subject = email_item.get("subject") or "(Không có chủ đề)"
            sender = email_item.get("sender") or "(Không rõ người gửi)"
            st.markdown(f"**{index}. {subject}**  \nTừ: `{sender}`")

st.markdown("---")

# -----------------------------------------------------------------------------
# STEP 3: Hiển Thị Kết Quả Công Việc (Task Results)
# -----------------------------------------------------------------------------
st.markdown(
    '### <span class="step-number">3</span> Kết Quả Công Việc Được Trích Xuất',
    unsafe_allow_html=True,
)

display_run_id = st.session_state.get("last_run_id")

if display_run_id:
    res_code, res_data = api_request("GET", f"/v1/mail-todo/runs/{display_run_id}/result")

    if res_code == 200 and isinstance(res_data, dict):
        # §6.6 Task contracts (T4.3): citations and missing information that
        # the legacy result shape does not carry.
        task_meta: dict[str, dict[str, Any]] = {}
        tasks_code, tasks_data = api_request(
            "GET", f"/v1/mail-todo/runs/{display_run_id}/tasks"
        )
        if tasks_code == 200 and isinstance(tasks_data, dict):
            task_meta = {
                str(task.get("gmail_message_id")): task
                for task in tasks_data.get("tasks", [])
            }
        items = res_data.get("actionItems", [])
        warnings = res_data.get("attachmentWarnings", [])

        if items:
            st.success(
                f"🎉 Đã tìm thấy và trích xuất **{len(items)} công việc** từ email chưa đọc!"
            )

            for idx, item in enumerate(items, 1):
                priority = (item.get("priority") or "MEDIUM").upper()
                p_class = f"priority-{priority.lower()}"
                bg_class = f"bg-{priority.lower()}"
                deadline = item.get("deadline_at") or item.get("deadline_text") or "Không rõ"
                confidence = (item.get("confidence") or "Không rõ").upper()
                title = item.get("title") or "(Không có tiêu đề)"
                summary = item.get("summary") or ""

                meta = task_meta.get(str(item.get("provider_message_id")), {})
                documents = meta.get("supporting_documents") or []
                missing_information = meta.get("missing_information") or []
                citations_by_id = {
                    str(doc.get("citation_id")): doc for doc in documents
                }
                # LLM-sourced fields: escape text and restrict href schemes
                # before interpolating into HTML.
                chip_fragments = []
                for doc in documents:
                    label = html.escape(str(doc.get("title") or ""), quote=True)
                    url = str(doc.get("url") or "")
                    if url.startswith(("http://", "https://")):
                        href = html.escape(url, quote=True)
                        chip_fragments.append(
                            f'<a class="citation-chip" href="{href}"'
                            f' target="_blank" rel="noopener">📎 {label}</a>'
                        )
                    else:
                        chip_fragments.append(
                            f'<span class="citation-chip">📎 {label}</span>'
                        )
                chips_html = "".join(chip_fragments)

                with st.container():
                    st.markdown(
                        f"""
                        <div class="task-card {p_class}">
                            <div style="display: flex; justify-content: space-between;
                                        align-items: center; margin-bottom: 8px;">
                                <strong style="font-size: 1.1rem; color: #1E293B;">
                                    {idx}. {title}
                                </strong>
                                <span class="badge {bg_class}">{priority}</span>
                            </div>
                            <p style="color: #475569; font-size: 0.95rem;
                                      margin-bottom: 8px;">{summary}</p>
                            <div style="margin-bottom: 8px;">{chips_html}</div>
                            <div style="font-size: 0.85rem; color: #64748B;">
                                <strong>Hạn chót:</strong> <code>{deadline}</code> | 
                                <strong>Độ tin cậy:</strong> <code>{confidence}</code>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    if missing_information:
                        st.warning(
                            "⚠️ Thiếu thông tin để hoàn tất kế hoạch: "
                            + "; ".join(str(gap) for gap in missing_information)
                        )

                    steps = item.get("action_plan", [])
                    if steps:
                        meta_steps = meta.get("action_plan") or []
                        with st.expander("📌 Các bước thực hiện chi tiết"):
                            for step_index, step in enumerate(steps):
                                instruction = step.get("instruction", "")
                                basis = step.get("basis")
                                basis_labels = {
                                    "email": "email",
                                    "attachment": "file đính kèm",
                                    "inference": "suy luận từ email",
                                    "suggestion": "đề xuất",
                                }
                                basis_label = basis_labels.get(basis, basis)
                                suffix = f" _(nguồn: {basis_label})_" if basis_label else ""
                                citation_ids = (
                                    meta_steps[step_index].get("supporting_citation_ids")
                                    if step_index < len(meta_steps)
                                    else None
                                ) or []
                                cited = [
                                    f"📎 {citations_by_id[str(cid)].get('title')}"
                                    for cid in citation_ids
                                    if str(cid) in citations_by_id
                                ]
                                citation_suffix = f" ({'; '.join(cited)})" if cited else ""
                                st.markdown(f"- {instruction}{suffix}{citation_suffix}")

                    col_link, col_meta = st.columns([1, 2])
                    with col_link:
                        if item.get("email_deep_link"):
                            st.link_button(
                                "↗️ Mở Email Trong Gmail", item.get("email_deep_link")
                            )
                    with col_meta:
                        sender = item.get("sender_name") or item.get("sender_address")
                        related_count = len(item.get("related_message_ids") or [])
                        related_suffix = (
                            f" · Tổng hợp từ {related_count} email" if related_count > 1 else ""
                        )
                        st.caption(
                            f"Email nguồn: {item.get('email_subject')} (Từ: {sender})"
                            f"{related_suffix}"
                        )

                    st.divider()

        else:
            st.info("💌 Không có công việc nào mới trong các email chưa đọc vừa quét.")

        if warnings:
            with st.expander("⚠️ Cảnh báo file đính kèm"):
                for w in warnings:
                    code = w.get("code")
                    code_suffix = f" [{code}]" if code else ""
                    st.warning(f"File {w.get('filename')}{code_suffix}: {w.get('message')}")

    else:
        st.error(f"Không thể lấy kết quả (Code: {res_code}): {res_data}")

else:
    st.info(
        "Chưa có kết quả nào. Bấm nút "
        "**'🚀 BẮT ĐẦU QUÉT MAIL & TẠO DANH SÁCH TASK'** ở Bước 2 để chạy!"
    )
