"""Cowork Demo — Streamlit showcase frontend (SPEC-Demo-Frontend Increment A).

Five-screen information architecture (SPEC §5) with the AI Chat Assistant as
the landing screen: Chat → Connect (Gmail + standalone Email Agent) →
Knowledge (RAG) → Memory (Increment B, feature-flagged) → Run audit.

The chat screen stays inside the SPEC §3.4 boundary: one server session per
browser session, idempotent messages, and typed SSE rendering only. Chat
transport lives in ``cowork_agent.gui.chat_client``.

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
from typing import Any, Literal

import httpx
from dotenv import load_dotenv

from cowork_agent.gui import chat_client

SUPPORTED_LANGUAGES = ("vi", "en")

#: SPEC-Demo-Frontend §5 information architecture; AI Chat is the landing screen.
SCREENS = ("chat", "connect", "knowledge", "memory", "audit")

#: SPEC-Demo-Frontend §7 endpoints that Increment B needs and the backend lacks.
MISSING_INCREMENT_B_ENDPOINTS = (
    "GET/POST/PUT/DELETE /v1/cowork/chat/profile",
    "GET /v1/cowork/chat/episodes",
    "GET /v1/cowork/chat/sessions",
    "GET /v1/cowork/chat/sessions/{id}/messages",
    "structured task-proposal SSE event or read DTO",
)

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
        "tasks_none_yet": ("Chưa có kết quả nào. Bấm nút **'{button}'** ở Bước 2 để chạy!"),
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
        "scan_mode_unread": "📨 Email chưa đọc trong tab Chính (Primary)",
        "scan_mode_all": (
            "🔄 Quét lại Top K email mới nhất trong Inbox (Test mode - bao gồm email đã đọc)"
        ),
        "scan_ordering_hint": (
            "💡 Hệ thống xếp email theo thời điểm nhận của Gmail (mới nhất trước). "
            "Hệ thống sẽ xử lý tối đa Top {max_emails} email mới nhất trong phạm vi đã chọn."
        ),
        "sort_label": "Sắp xếp theo:",
        "sort_priority_desc": "Mức ưu tiên: Cao ➔ Thấp",
        "sort_priority_asc": "Mức ưu tiên: Thấp ➔ Cao",
        "sort_deadline": "Hạn chót (Gần nhất)",
        "sort_default": "Thứ tự hệ thống",
        "filter_priority_label": "Lọc theo mức ưu tiên:",
        "filter_all": "Tất cả",
        "knowledge_title": "Kho Tri Thức (RAG)",
        "knowledge_ready": "Sẵn sàng",
        "knowledge_degraded": "Suy giảm (chỉ BM25, không có embedding)",
        "knowledge_unavailable": "Không khả dụng",
        "knowledge_docs_heading": "Tài liệu đã nạp",
        "knowledge_doc_title": "Tên",
        "knowledge_doc_sections": "Số phần",
        "knowledge_doc_source": "Nguồn",
        "knowledge_empty_corpus": "Chưa có tài liệu nào được nạp vào kho tri thức.",
        "knowledge_query_placeholder": "Nhập câu hỏi để tra cứu trong kho tri thức…",
        "knowledge_query_button": "🔍 Tra cứu",
        "knowledge_results_heading": "Kết quả tra cứu",
        "knowledge_no_results": "Không tìm thấy kết quả phù hợp.",
        "knowledge_fetch_error": "Không thể tra cứu tri thức (mã {code}).",
        "knowledge_score_label": "Điểm",
        "knowledge_reranker_label": "Reranker",
        "knowledge_reranker_on": "Có",
        "knowledge_reranker_off": "Không",
        "knowledge_latency": "Độ trễ: {ms} ms",
        "knowledge_status_label": "Trạng thái kho tri thức",
        "knowledge_doc_count": "{count} tài liệu",
        "knowledge_chunk_count": "{count} chunks",
        "nav_label": "Màn hình",
        "nav_chat": "💬 Trợ Lý AI Chat",
        "nav_connect": "🔗 Kết nối",
        "nav_knowledge": "📚 Kho tri thức",
        "nav_memory": "🧠 Bộ nhớ",
        "nav_audit": "🔎 Kiểm toán Run",
        "chat_title": "Trợ Lý AI Chat",
        "chat_subtitle": (
            "Trò chuyện nhiều lượt với Cowork. Bộ nhớ đủ điều kiện sẽ được gắn nhãn "
            "ngay trong luồng hội thoại."
        ),
        "chat_input_placeholder": "Nhập tin nhắn cho trợ lý…",
        "chat_empty": "Chưa có tin nhắn nào. Hãy bắt đầu cuộc trò chuyện bên dưới.",
        "chat_session_label": "Phiên chat hiện tại",
        "chat_session_creating": "Đang tạo phiên chat…",
        "chat_session_error": "Không thể tạo phiên chat (mã {code}).",
        "chat_session_error_help": (
            "Kiểm tra backend đã bật và đã có đúng một tài khoản Gmail được kết nối "
            "(phiên chat dùng danh tính đã xác thực đó)."
        ),
        "chat_new_session": "🗘 Phiên mới",
        "chat_thinking": "Trợ lý đang trả lời…",
        "chat_no_reply": "Trợ lý không trả về nội dung nào cho lượt này.",
        "chat_badges_title": "Nguồn bộ nhớ đã dùng",
        "chat_badge_declarative": "Hồ sơ cá nhân",
        "chat_badge_episodic": "Episode đã duyệt",
        "chat_badge_semantic": "Tri thức công ty",
        "chat_error_title": "Lượt chat thất bại [{code}]",
        "chat_error_stream_unavailable": "Mất kết nối tới luồng chat của backend.",
        "chat_error_http": "Backend từ chối lượt chat này.",
        "chat_error_identity": (
            "Backend không có đúng một kết nối Gmail active; chat cần đúng một "
            "danh tính đã xác thực. Kiểm tra màn hình Kết nối."
        ),
        "chat_advisory_memory_degraded": (
            "⚠️ Một phần bộ nhớ tùy chọn không khả dụng; câu trả lời vẫn tiếp tục "
            "nhưng có thể kém cá nhân hóa hơn."
        ),
        "chat_session_renewed": (
            "♻️ Phiên chat cũ đã hết hạn trên server; đã tự động tạo phiên mới để "
            "tiếp tục."
        ),
        "chat_proposal_title": "📋 Đề xuất task trong chat",
        "chat_proposal_plan": "Kế hoạch hành động",
        "chat_proposal_missing": "Thiếu thông tin",
        "chat_proposal_citations": "Trích dẫn tri thức công ty",
        "chat_proposal_status": "Trạng thái",
        "chat_action_approve": "✅ Duyệt",
        "chat_action_complete": "🏁 Hoàn thành",
        "chat_action_reject": "❌ Từ chối",
        "chat_action_failed": "Thao tác task thất bại (mã {code}).",
        "chat_history_label": "Phiên chat trên server",
        "chat_load_history": "📥 Nạp lịch sử phiên",
        "chat_history_note": (
            "ℹ️ Lịch sử chat đọc từ backend; danh sách phiên nằm trong bộ nhớ "
            "server nên mất khi backend khởi động lại."
        ),
        "memory_prefs_title": "👤 Hồ sơ cá nhân (Preferences)",
        "memory_field_language": "Ngôn ngữ",
        "memory_field_timezone": "Múi giờ",
        "memory_field_persona": "Persona trợ lý",
        "memory_field_tone": "Giọng điệu trả lời",
        "memory_save": "💾 Lưu hồ sơ",
        "memory_delete_profile": "🗑 Xóa hồ sơ",
        "memory_saved_flash": "Đã lưu hồ sơ cá nhân.",
        "memory_deleted_flash": "Đã xóa hồ sơ cá nhân.",
        "memory_episodes_title": "🗂 Episode task",
        "memory_refresh": "🗘 Làm mới",
        "memory_episode_delete": "🗑 Xóa",
        "memory_episodes_empty": "Chưa có episode nào.",
        "memory_episode_deleted": "Đã xóa episode.",
        "memory_episode_delete_failed": "Xóa episode thất bại (mã {code}).",
        "memory_store_unavailable": (
            "Backend chưa bật kho nhớ PostgreSQL (DATABASE_URL); màn hình Bộ nhớ "
            "tạm khóa."
        ),
        "chat_retry": "↻ Thử lại lượt vừa rồi",
        "chat_retry_hint": ("Thử lại dùng lại đúng khóa idempotency nên sẽ không tạo lượt trùng."),
        "memory_locked_title": "Màn hình Bộ nhớ chưa mở",
        "memory_locked_body": (
            "Increment B (hồ sơ cá nhân, thẻ task trong chat, provenance episode, xóa "
            "bộ nhớ) chờ các contract backend còn thiếu theo SPEC §7. Demo không dựng "
            "giao diện giả cho phần chưa có."
        ),
        "memory_missing_heading": "Còn thiếu ở backend:",
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
        "scan_mode_unread": "📨 Unread emails in the Primary tab",
        "scan_mode_all": (
            "🔄 Re-process Top K recent emails in Inbox (Test mode - includes read emails)"
        ),
        "scan_ordering_hint": (
            "💡 The system ranks emails by Gmail received time (newest first). "
            "It processes up to the top {max_emails} emails in the selected scope."
        ),
        "sort_label": "Sort by:",
        "sort_priority_desc": "Priority: High ➔ Low",
        "sort_priority_asc": "Priority: Low ➔ High",
        "sort_deadline": "Deadline (Soonest)",
        "sort_default": "Default order",
        "filter_priority_label": "Filter by priority:",
        "filter_all": "All",
        "knowledge_title": "Knowledge Base (RAG)",
        "knowledge_ready": "Ready",
        "knowledge_degraded": "Degraded (BM25 only, no embeddings)",
        "knowledge_unavailable": "Unavailable",
        "knowledge_docs_heading": "Loaded documents",
        "knowledge_doc_title": "Title",
        "knowledge_doc_sections": "Sections",
        "knowledge_doc_source": "Source",
        "knowledge_empty_corpus": "No documents loaded into the knowledge base.",
        "knowledge_query_placeholder": "Enter a query to search the knowledge base…",
        "knowledge_query_button": "🔍 Search",
        "knowledge_results_heading": "Retrieval results",
        "knowledge_no_results": "No matching results found.",
        "knowledge_fetch_error": "Could not query the knowledge base (code {code}).",
        "knowledge_score_label": "Score",
        "knowledge_reranker_label": "Reranker",
        "knowledge_reranker_on": "On",
        "knowledge_reranker_off": "Off",
        "knowledge_latency": "Latency: {ms} ms",
        "knowledge_status_label": "Corpus status",
        "knowledge_doc_count": "{count} documents",
        "knowledge_chunk_count": "{count} chunks",
        "nav_label": "Screen",
        "nav_chat": "💬 AI Chat Assistant",
        "nav_connect": "🔗 Connect",
        "nav_knowledge": "📚 Knowledge",
        "nav_memory": "🧠 Memory",
        "nav_audit": "🔎 Run audit",
        "chat_title": "AI Chat Assistant",
        "chat_subtitle": (
            "Multi-turn conversation with Cowork. Eligible memory is labeled inline in the thread."
        ),
        "chat_input_placeholder": "Message the assistant…",
        "chat_empty": "No messages yet. Start the conversation below.",
        "chat_session_label": "Active chat session",
        "chat_session_creating": "Creating chat session…",
        "chat_session_error": "Could not create a chat session (code {code}).",
        "chat_session_error_help": (
            "Check that the backend is running and exactly one Gmail account is "
            "connected — the chat session uses that verified identity."
        ),
        "chat_new_session": "🗘 New session",
        "chat_thinking": "Assistant is replying…",
        "chat_no_reply": "The assistant returned no content for this turn.",
        "chat_badges_title": "Memory sources used",
        "chat_badge_declarative": "Personal profile",
        "chat_badge_episodic": "Approved episode",
        "chat_badge_semantic": "Company knowledge",
        "chat_error_title": "Chat turn failed [{code}]",
        "chat_error_stream_unavailable": "Lost the connection to the backend chat stream.",
        "chat_error_http": "The backend rejected this chat turn.",
        "chat_error_identity": (
            "The backend does not have exactly one active Gmail connection; chat needs "
            "exactly one verified identity. Check the Connect screen."
        ),
        "chat_advisory_memory_degraded": (
            "⚠️ Some optional memory was unavailable; the reply continues but may "
            "be less personalized."
        ),
        "chat_session_renewed": (
            "♻️ The previous chat session expired on the server; a new one was "
            "created automatically to continue."
        ),
        "chat_proposal_title": "📋 In-chat task proposal",
        "chat_proposal_plan": "Action plan",
        "chat_proposal_missing": "Missing information",
        "chat_proposal_citations": "Company knowledge citations",
        "chat_proposal_status": "Status",
        "chat_action_approve": "✅ Approve",
        "chat_action_complete": "🏁 Complete",
        "chat_action_reject": "❌ Reject",
        "chat_action_failed": "Task action failed (code {code}).",
        "chat_history_label": "Server chat sessions",
        "chat_load_history": "📥 Load session history",
        "chat_history_note": (
            "ℹ️ Chat history is read from the backend; the session list lives in "
            "server memory, so it is lost when the backend restarts."
        ),
        "memory_prefs_title": "👤 Persona profile (Preferences)",
        "memory_field_language": "Language",
        "memory_field_timezone": "Timezone",
        "memory_field_persona": "Assistant persona",
        "memory_field_tone": "Response tone",
        "memory_save": "💾 Save profile",
        "memory_delete_profile": "🗑 Delete profile",
        "memory_saved_flash": "Profile saved.",
        "memory_deleted_flash": "Profile deleted.",
        "memory_episodes_title": "🗂 Task episodes",
        "memory_refresh": "🗘 Refresh",
        "memory_episode_delete": "🗑 Delete",
        "memory_episodes_empty": "No episodes yet.",
        "memory_episode_deleted": "Episode deleted.",
        "memory_episode_delete_failed": "Episode deletion failed (code {code}).",
        "memory_store_unavailable": (
            "The backend has no PostgreSQL memory store (DATABASE_URL); the Memory "
            "screen stays locked."
        ),
        "chat_retry": "↻ Retry the last turn",
        "chat_retry_hint": (
            "Retrying reuses the same idempotency key, so it cannot create a duplicate turn."
        ),
        "memory_locked_title": "Memory screen not unlocked",
        "memory_locked_body": (
            "Increment B (persona profile, in-chat task cards, episode provenance, "
            "memory deletion) is waiting on backend contracts still missing per SPEC "
            "§7. The demo does not mock UI for work that does not exist."
        ),
        "memory_missing_heading": "Missing from the backend:",
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
.memory-badge {
    display: inline-block;
    font-size: 0.75rem;
    font-weight: 600;
    border-radius: 9999px;
    padding: 2px 10px;
    margin: 2px 4px 2px 0;
    border: 1px solid transparent;
}
.memory-declarative { background-color: #E0F2FE; color: #075985; border-color: #BAE6FD; }
.memory-episodic { background-color: #DCFCE7; color: #166534; border-color: #BBF7D0; }
.memory-semantic { background-color: #DBEAFE; color: #1E40AF; border-color: #BFDBFE; }
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
        f'<a class="citation-chip" href="{escaped}" target="_blank" rel="noopener">📎 {label}</a>'
    )


def memory_badges_html(citations: Sequence[tuple[str, str]], lang: str) -> str:
    """Render one badge per cited memory kind, with a count when repeated.

    ``source_id`` is deliberately never rendered: SPEC §7.1 treats it as opaque,
    so the badge discloses the source *type*, not the stored payload.
    """
    counts: dict[str, int] = {}
    for memory_type, _source_id in citations:
        if memory_type in chat_client.MEMORY_BADGES:
            counts[memory_type] = counts.get(memory_type, 0) + 1
    badges = []
    for memory_type, count in counts.items():
        label = tr(lang, chat_client.MEMORY_BADGES[memory_type]["key"])
        badges.append(
            chat_client.memory_badge_html(memory_type, label if count == 1 else f"{label} ×{count}")
        )
    return "".join(badges)


def chat_error_text(code: object, safe_message: object, lang: str) -> str:
    """Pick the copy for a failed turn; backend safe messages win when present."""
    if isinstance(safe_message, str) and safe_message.strip():
        return safe_message
    code_text = str(code or "")
    if code_text == "stream_unavailable":
        return tr(lang, "chat_error_stream_unavailable")
    if code_text == "http_503":
        return tr(lang, "chat_error_identity")
    if code_text.startswith("http_"):
        return tr(lang, "chat_error_http")
    return tr(lang, "error_unknown")


def chat_advisory_text(code: object, lang: str) -> str:
    """Localized copy for a non-terminal memory advisory (warn, not fail)."""
    if code == "optional_memory_degraded":
        return tr(lang, "chat_advisory_memory_degraded")
    return tr(lang, "error_unknown")


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


def build_proposal_card_html(proposal: Mapping[str, Any], lang: str) -> str:
    """Safe HTML for the structured chat-native task proposal (SPEC §6.11)."""
    title = html.escape(str(proposal.get("task_title") or ""), quote=True)
    paraphrase = html.escape(
        str(proposal.get("minimal_request_paraphrase") or ""), quote=True
    )
    status = str(proposal.get("validation_status") or "system_generated")
    status_text = html.escape(enum_label("validation_status", status, lang), quote=True)
    missing = [str(item) for item in _as_list(proposal.get("missing_information"))]
    partial_class = " partial-plan" if missing else ""
    sections = [
        f'<div class="task-card{partial_class}">',
        '<div style="display: flex; justify-content: space-between;'
        ' align-items: center; margin-bottom: 8px;">'
        f'<strong style="font-size: 1.05rem; color: #1E293B;">'
        f"{html.escape(tr(lang, 'chat_proposal_title'), quote=True)}</strong>"
        f'<span class="badge bg-route">{status_text}</span></div>',
        f'<p style="color: #1E293B; font-weight: 600; margin-bottom: 4px;">{title}</p>',
        f'<p style="color: #475569; font-size: 0.9rem; margin-bottom: 8px;">{paraphrase}</p>',
    ]
    plan = [str(item) for item in _as_list(proposal.get("action_plan"))]
    if plan:
        items = "".join(
            f"<li>{html.escape(step, quote=True)}</li>" for step in plan
        )
        sections.append(
            f"<div style=\"font-size: 0.85rem; color: #334155; margin-bottom: 8px;\">"
            f"<strong>{html.escape(tr(lang, 'chat_proposal_plan'), quote=True)}:"
            f"</strong><ol style=\"margin: 4px 0 0 18px;\">{items}</ol></div>"
        )
    if missing:
        items = "".join(
            f"<li>{html.escape(entry, quote=True)}</li>" for entry in missing
        )
        sections.append(
            f"<div style=\"font-size: 0.85rem; color: #92400E; margin-bottom: 8px;\">"
            f"<strong>{html.escape(tr(lang, 'chat_proposal_missing'), quote=True)}:"
            f"</strong><ul style=\"margin: 4px 0 0 18px;\">{items}</ul></div>"
        )
    citations = [item for item in _as_list(proposal.get("rag_citations"))]
    chips = []
    for citation in citations:
        if not isinstance(citation, Mapping):
            continue
        doc_title = citation.get("document_title")
        section = citation.get("section")
        label = str(doc_title or "") + (f" · {section}" if section else "")
        chips.append(citation_chip_html(label, citation.get("source_url")))
    if chips:
        sections.append(
            f"<div style=\"margin-bottom: 4px;\"><strong style=\"font-size: 0.85rem; "
            f"color: #334155;\">"
            f"{html.escape(tr(lang, 'chat_proposal_citations'), quote=True)}:"
            f"</strong><br/>{''.join(chips)}</div>"
        )
    sections.append("</div>")
    return "".join(sections)


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
            return httpx.Client(timeout=20.0)

        return _cached_client()
    except Exception:
        return httpx.Client(timeout=20.0)


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

        @st.cache_data(ttl=15.0, show_spinner=False)
        def _cached_health(url: str) -> tuple[int, Any]:
            return api_request(url, "GET", "/health")

        return _cached_health(base_url)
    except Exception:
        return api_request(base_url, "GET", "/health")


def read_settings() -> dict[str, Any]:
    load_dotenv(override=False)
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
        "chat_stream_timeout_seconds": max(
            5.0, float(os.getenv("STREAMLIT_CHAT_TIMEOUT_SECONDS", "90"))
        ),
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
        page_title="Cowork Demo",
        page_icon="💬",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    st.markdown(_GUI_CSS, unsafe_allow_html=True)

    col_title, col_lang = st.columns([4, 1])
    with col_lang:
        lang = str(
            st.selectbox(
                "🌐 Language / Ngôn ngữ",
                options=list(SUPPORTED_LANGUAGES),
                index=0,
                format_func=str.upper,
                key="demo_language",
            )
        )

    screen = str(
        st.sidebar.radio(
            tr(lang, "nav_label"),
            options=list(SCREENS),
            format_func=lambda key: tr(lang, f"nav_{key}"),
            key="demo_screen",
        )
    )

    with col_title:
        st.markdown(
            f'<div class="main-title">{tr(lang, "nav_" + screen)}</div>', unsafe_allow_html=True
        )
        subtitle = tr(lang, "chat_subtitle") if screen == "chat" else tr(lang, "subtitle")
        st.markdown(f'<div class="subtitle">{subtitle}</div>', unsafe_allow_html=True)

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

    if screen == "chat":
        _screen_chat(base_url, lang, settings)
    elif screen == "connect":
        connections = _load_connections(base_url, lang)
        _screen_connect(base_url, lang, connections)
        st.markdown("---")
        _screen_run(base_url, lang, settings, connections)
        st.markdown("---")
        _screen_tasks(base_url, lang)
    elif screen == "knowledge":
        _screen_knowledge(base_url, lang)
    elif screen == "memory":
        _screen_memory(base_url, lang)
    else:
        _screen_audit(base_url, lang)

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    st.caption(f"⚡ *Streamlit Rerun Latency: {elapsed_ms:.1f} ms*")


def _load_connections(base_url: str, lang: str) -> list[dict[str, Any]]:
    import streamlit as st

    try:

        @st.cache_data(ttl=30.0, show_spinner=False)
        def _cached_connections(url: str) -> tuple[int, Any]:
            return api_request(url, "GET", "/v1/mail-todo/connections")

        code, res = _cached_connections(base_url)
    except Exception:
        code, res = api_request(base_url, "GET", "/v1/mail-todo/connections")

    if code == 200 and isinstance(res, dict):
        return [dict(item) for item in _as_list(res.get("connections"))]
    st.error(tr(lang, "connections_error", code=code))
    return []


def _screen_chat(base_url: str, lang: str, settings: dict[str, Any]) -> None:
    """Screen 1: AI Chat Assistant, limited to the SPEC §3.4 boundary."""
    import streamlit as st

    session_id = _ensure_chat_session(base_url, lang)
    if session_id is None:
        return

    st.sidebar.caption(f"{tr(lang, 'chat_session_label')}: `{session_id[:8]}…`")
    if st.sidebar.button(tr(lang, "chat_new_session"), use_container_width=True):
        _reset_chat_session()
        st.rerun()
    _render_session_history(base_url, lang, session_id)

    st.caption(tr(lang, "chat_history_note"))

    messages: list[dict[str, Any]] = st.session_state.setdefault("chat_messages", [])
    if not messages:
        st.info(tr(lang, "chat_empty"))
    for message in messages:
        _render_chat_message(message, lang, base_url=base_url, session_id=session_id)

    # A failed turn keeps its idempotency key so a retry stays one logical turn.
    pending = st.session_state.get("chat_pending_turn")
    if isinstance(pending, dict):
        st.caption(tr(lang, "chat_retry_hint"))
        if st.button(tr(lang, "chat_retry"), type="primary"):
            _run_chat_turn(
                base_url,
                lang,
                settings,
                session_id,
                str(pending["user_message"]),
                str(pending["idempotency_key"]),
            )
            st.rerun()

    prompt = st.chat_input(tr(lang, "chat_input_placeholder"))
    if not prompt or not prompt.strip():
        return

    user_message = prompt.strip()
    messages.append({"role": "user", "text": user_message})
    _render_chat_message(messages[-1], lang)
    _run_chat_turn(
        base_url,
        lang,
        settings,
        session_id,
        user_message,
        chat_client.new_idempotency_key(),
    )
    st.rerun()


def _render_session_history(base_url: str, lang: str, session_id: str) -> None:
    """Sidebar switcher over the backend session list (GET /sessions)."""
    import streamlit as st

    code, body = api_request(base_url, "GET", chat_client.CHAT_SESSIONS_PATH)
    if code != 200 or not isinstance(body, Mapping):
        return
    sessions = [
        str(item.get("session_id"))
        for item in _as_list(body.get("sessions"))
        if isinstance(item, Mapping) and item.get("session_id")
    ]
    if not sessions:
        return
    labels = [f"{sid[:8]}…" for sid in sessions]
    current_index = next(
        (i for i, sid in enumerate(sessions) if sid == session_id), 0
    )
    chosen_label = st.sidebar.selectbox(
        tr(lang, "chat_history_label"),
        labels,
        index=current_index,
        key="chat_history_select",
    )
    chosen = sessions[labels.index(chosen_label)]
    if chosen == session_id:
        return
    if st.sidebar.button(tr(lang, "chat_load_history"), use_container_width=True):
        history_code, history = api_request(
            base_url, "GET", f"{chat_client.CHAT_SESSIONS_PATH}/{chosen}/messages"
        )
        if history_code != 200 or not isinstance(history, Mapping):
            st.session_state["flash"] = {
                "message": tr(lang, "chat_action_failed", code=history_code),
                "level": "error",
            }
            st.rerun()
        reloaded: list[dict[str, Any]] = []
        for turn in _as_list(history.get("turns")):
            if not isinstance(turn, Mapping):
                continue
            reloaded.append(
                {"role": "user", "text": str(turn.get("user_message") or "")}
            )
            reloaded.append(
                {"role": "assistant", "text": str(turn.get("assistant_message") or "")}
            )
        st.session_state["chat_session_id"] = chosen
        st.session_state["chat_messages"] = reloaded
        st.session_state.pop("chat_pending_turn", None)
        st.rerun()


def _ensure_chat_session(base_url: str, lang: str) -> str | None:
    """Return the browser session's server chat session, creating it once."""
    import streamlit as st

    session_id = st.session_state.get("chat_session_id")
    if isinstance(session_id, str) and session_id:
        return session_id

    with st.spinner(tr(lang, "chat_session_creating")):
        code, created = chat_client.create_chat_session(_get_http_client(), base_url)
    if created is None:
        st.error(tr(lang, "chat_session_error", code=code))
        st.info(tr(lang, "chat_session_error_help"))
        return None
    st.session_state["chat_session_id"] = created
    st.session_state["chat_messages"] = []
    st.session_state.pop("chat_pending_turn", None)
    return created


def _reset_chat_session() -> None:
    import streamlit as st

    for key in ("chat_session_id", "chat_messages", "chat_pending_turn"):
        st.session_state.pop(key, None)


def _render_chat_message(
    message: dict[str, Any],
    lang: str,
    *,
    base_url: str | None = None,
    session_id: str | None = None,
) -> None:
    """Render one stored turn: assistant text, memory badges, and error state."""
    import streamlit as st

    role = str(message.get("role") or "assistant")
    with st.chat_message(role):
        text = str(message.get("text") or "")
        if text:
            st.markdown(text)
        citations = [
            (str(kind), str(source)) for kind, source in _as_list(message.get("citations"))
        ]
        if citations:
            st.caption(tr(lang, "chat_badges_title"))
            st.markdown(memory_badges_html(citations, lang), unsafe_allow_html=True)
        proposal = message.get("proposal")
        if isinstance(proposal, Mapping):
            st.markdown(
                build_proposal_card_html(proposal, lang), unsafe_allow_html=True
            )
            if base_url is not None and session_id is not None:
                _render_proposal_controls(message, proposal, lang, base_url, session_id)
        advisory_code = message.get("advisory_code")
        if advisory_code:
            st.warning(chat_advisory_text(advisory_code, lang))
        error_code = message.get("error_code")
        if error_code:
            st.error(
                f"{tr(lang, 'chat_error_title', code=error_code)}: "
                f"{chat_error_text(error_code, message.get('error_message'), lang)}"
            )
        elif not text and not isinstance(proposal, Mapping):
            st.info(tr(lang, "chat_no_reply"))


def _render_proposal_controls(
    message: dict[str, Any],
    proposal: Mapping[str, Any],
    lang: str,
    base_url: str,
    session_id: str,
) -> None:
    """Inline Approve/Complete/Reject on a structured proposal (SPEC §3.2)."""
    import streamlit as st

    episode_id = str(proposal.get("episode_id") or "")
    status = str(proposal.get("validation_status") or "")
    if not episode_id:
        return
    if status == "system_generated":
        actions: tuple[tuple[str, str, Literal["primary", "secondary"]], ...] = (
            ("approve", "chat_action_approve", "primary"),
            ("reject", "chat_action_reject", "secondary"),
        )
    elif status == "user_approved":
        actions = (("complete", "chat_action_complete", "primary"),)
    else:
        return
    columns = st.columns(len(actions))
    for column, (action, label_key, kind) in zip(columns, actions, strict=True):
        if column.button(
            tr(lang, label_key),
            key=f"chat_ep_{episode_id}_{action}",
            type=kind,
        ):
            code, body = api_request(
                base_url,
                "POST",
                f"/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/{action}",
            )
            if code == 200 and isinstance(body, Mapping):
                message["proposal"] = {
                    **proposal,
                    "validation_status": body.get("validation_status", status),
                    "retrieval_eligible": body.get("retrieval_eligible"),
                }
            else:
                st.session_state["flash"] = {
                    "message": tr(lang, "chat_action_failed", code=code),
                    "level": "error",
                }
            st.rerun()


def _run_chat_turn(
    base_url: str,
    lang: str,
    settings: dict[str, Any],
    session_id: str,
    user_message: str,
    idempotency_key: str,
) -> None:
    """Stream one assistant turn and fold it into browser-session state."""
    import streamlit as st

    accumulator = chat_client.ChatTurnAccumulator()
    active_session = session_id
    renewed = False
    with st.chat_message("assistant"):
        text_slot = st.empty()
        badge_slot = st.empty()
        warn_slot = st.empty()
        proposal_slot = st.empty()
        for _attempt in range(2):
            with st.spinner(tr(lang, "chat_thinking")):
                events = chat_client.stream_chat_turn(
                    _get_http_client(),
                    base_url,
                    session_id=active_session,
                    user_message=user_message,
                    idempotency_key=idempotency_key,
                    timeout_seconds=float(settings["chat_stream_timeout_seconds"]),
                )
                for event in events:
                    if not accumulator.apply(event):
                        continue
                    if event.event_type == "delta":
                        text_slot.markdown(accumulator.text)
                    elif event.event_type == "memory_citation":
                        badge_slot.markdown(
                            memory_badges_html(accumulator.citations, lang),
                            unsafe_allow_html=True,
                        )
                    elif event.event_type == "task_proposal" and event.proposal is not None:
                        proposal_slot.markdown(
                            build_proposal_card_html(event.proposal, lang),
                            unsafe_allow_html=True,
                        )
                    elif event.event_type == "error" and event.code in (
                        chat_client.ADVISORY_ERROR_CODES
                    ):
                        warn_slot.warning(chat_advisory_text(event.code, lang))
                    if accumulator.is_terminal:
                        break
            if _dead_session(accumulator):
                # The server forgot the session (backend restart); one bounded
                # renewal keeps retry honest instead of a permanent dead end.
                fresh = _renew_chat_session(base_url)
                if fresh is None:
                    break
                active_session = fresh
                renewed = True
                accumulator = chat_client.ChatTurnAccumulator()
                text_slot.empty()
                badge_slot.empty()
                warn_slot.empty()
                proposal_slot.empty()
                continue
            break
        if renewed and accumulator.error_code is None:
            st.info(tr(lang, "chat_session_renewed"))

    messages: list[dict[str, Any]] = st.session_state.setdefault("chat_messages", [])
    messages.append(accumulator.to_message())
    if accumulator.error_code is None:
        st.session_state.pop("chat_pending_turn", None)
    else:
        st.session_state["chat_pending_turn"] = {
            "user_message": user_message,
            "idempotency_key": idempotency_key,
        }


def _dead_session(accumulator: chat_client.ChatTurnAccumulator) -> bool:
    return (
        accumulator.error_code == "http_404"
        and not accumulator.text
        and not accumulator.completed
    )


def _renew_chat_session(base_url: str) -> str | None:
    """Replace a server-side-expired session id, keeping displayed history."""
    import streamlit as st

    st.session_state.pop("chat_session_id", None)
    st.session_state.pop("chat_pending_turn", None)
    _, created = chat_client.create_chat_session(_get_http_client(), base_url)
    if created is None:
        return None
    st.session_state["chat_session_id"] = created
    return created


def _screen_memory(base_url: str, lang: str) -> None:
    """Screen 4: Increment B memory administration (SPEC §3.2)."""
    import streamlit as st

    code, body = api_request(base_url, "GET", "/v1/cowork/chat/episodes")
    if code == 503:
        st.warning(f"🔒 {tr(lang, 'memory_locked_title')}")
        st.info(tr(lang, "memory_store_unavailable"))
        return
    if code != 200:
        st.warning(f"🔒 {tr(lang, 'memory_locked_title')}")
        st.info(tr(lang, "memory_locked_body"))
        st.markdown(f"**{tr(lang, 'memory_missing_heading')}**")
        for endpoint in MISSING_INCREMENT_B_ENDPOINTS:
            st.markdown(f"- `{endpoint}`")
        return

    episodes = [item for item in _as_list(body.get("episodes")) if isinstance(item, Mapping)]
    _render_preferences(base_url, lang)
    st.markdown("---")
    st.markdown(f"### {tr(lang, 'memory_episodes_title')}")
    if st.button(tr(lang, "memory_refresh")):
        st.rerun()
    if not episodes:
        st.info(tr(lang, "memory_episodes_empty"))
    for episode in episodes:
        _render_episode_row(base_url, lang, episode)


def _render_preferences(base_url: str, lang: str) -> None:
    import streamlit as st

    st.markdown(f"### {tr(lang, 'memory_prefs_title')}")
    code, profile = api_request(base_url, "GET", "/v1/cowork/chat/profile")
    current: Mapping[str, Any] = profile if code == 200 and isinstance(profile, Mapping) else {}
    with st.form("memory_preferences"):
        language = st.text_input(
            tr(lang, "memory_field_language"),
            value=str(current.get("language") or ""),
            max_chars=200,
        )
        timezone = st.text_input(
            tr(lang, "memory_field_timezone"),
            value=str(current.get("timezone") or ""),
            max_chars=200,
        )
        persona = st.text_input(
            tr(lang, "memory_field_persona"),
            value=str(current.get("assistant_persona") or ""),
            max_chars=200,
        )
        tone = st.text_input(
            tr(lang, "memory_field_tone"),
            value=str(current.get("response_tone") or ""),
            max_chars=200,
        )
        submitted = st.form_submit_button(tr(lang, "memory_save"), type="primary")
    if submitted:
        payload = {
            "language": language or None,
            "timezone": timezone or None,
            "assistant_persona": persona or None,
            "response_tone": tone or None,
        }
        method = "PUT" if code == 200 else "POST"
        save_code, _ = api_request(base_url, method, "/v1/cowork/chat/profile", json=payload)
        st.session_state["flash"] = {
            "message": (
                tr(lang, "memory_saved_flash")
                if save_code in (200, 201)
                else tr(lang, "chat_action_failed", code=save_code)
            ),
            "level": "success" if save_code in (200, 201) else "error",
        }
        st.rerun()
    if code == 200:
        confirm = st.checkbox(tr(lang, "memory_delete_profile"))
        if confirm:
            delete_code, _ = api_request(base_url, "DELETE", "/v1/cowork/chat/profile")
            st.session_state["flash"] = {
                "message": (
                    tr(lang, "memory_deleted_flash")
                    if delete_code == 204
                    else tr(lang, "chat_action_failed", code=delete_code)
                ),
                "level": "success" if delete_code == 204 else "error",
            }
            st.rerun()


def _render_episode_row(
    base_url: str, lang: str, episode: Mapping[str, Any]
) -> None:
    import streamlit as st

    episode_id = str(episode.get("episode_id") or "")
    status = str(episode.get("validation_status") or "unknown")
    title = str(episode.get("task_title") or "")
    created = str(episode.get("created_at") or "")[:19].replace("T", " ")
    provenance = " · ".join(
        part
        for part in (
            str(episode.get("model_id") or ""),
            f"pipeline {episode.get('pipeline_version')}"
            if episode.get("pipeline_version") is not None
            else "",
        )
        if part
    )
    missing_count = len(_as_list(episode.get("missing_information")))
    with st.container(border=True):
        st.markdown(
            f"**{html.escape(title)}** — "
            f"{html.escape(enum_label('validation_status', status, lang))}"
            + (f" · `{created}`" if created else "")
            + (f" · {html.escape(provenance)}" if provenance else "")
            + (f" · ⚠️ {missing_count}" if missing_count else ""),
            unsafe_allow_html=True,
        )
        if st.button(
            tr(lang, "memory_episode_delete"),
            key=f"memory_episode_delete_{episode_id}",
        ):
            session_id = str(episode.get("chat_session_id") or "")
            delete_code, _ = api_request(
                base_url,
                "DELETE",
                f"/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}",
            )
            st.session_state["flash"] = {
                "message": (
                    tr(lang, "memory_episode_deleted")
                    if delete_code == 204
                    else tr(lang, "memory_episode_delete_failed", code=delete_code)
                ),
                "level": "success" if delete_code == 204 else "error",
            }
            st.rerun()


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
            tr(lang, "connect_gmail"),
            connect_url,
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
            row_left.markdown(f"📫 **{html.escape(email)}** · `{html.escape(status)}`")
            if row_right.button(
                tr(lang, "disconnect"),
                key=f"disconnect-{connection.get('id')}",
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
        selected_query = (
            "is:unread in:inbox category:primary" if scan_mode == "unread" else "in:inbox"
        )

        max_emails = st.slider(
            tr(lang, "max_emails_slider"), min_value=5, max_value=100, value=20, step=5
        )
        st.caption(tr(lang, "scan_ordering_hint", max_emails=max_emails))

        start_clicked = st.button(
            tr(lang, "start_run"),
            type="primary",
            use_container_width=True,
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
                base_url,
                "POST",
                "/v1/mail-todo/runs",
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
                lang,
                "run_progress",
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


def _fetch_knowledge_status(base_url: str) -> tuple[int, Any]:
    try:
        import streamlit as st

        @st.cache_data(ttl=10.0, show_spinner=False)
        def _cached(url: str) -> tuple[int, Any]:
            return api_request(url, "GET", "/v1/mail-todo/knowledge/ready")

        return _cached(base_url)
    except Exception:
        return api_request(base_url, "GET", "/v1/mail-todo/knowledge/ready")


def _fetch_knowledge_documents(base_url: str) -> tuple[int, Any]:
    try:
        import streamlit as st

        @st.cache_data(ttl=300.0, show_spinner=False)
        def _cached(url: str) -> tuple[int, Any]:
            return api_request(url, "GET", "/v1/mail-todo/knowledge/documents")

        return _cached(base_url)
    except Exception:
        return api_request(base_url, "GET", "/v1/mail-todo/knowledge/documents")


def _post_knowledge_chat(base_url: str, query: str, top_k: int = 5) -> tuple[int, Any]:
    return api_request(
        base_url,
        "POST",
        "/v1/mail-todo/knowledge/chat",
        json={"query": query, "top_k": top_k},
    )


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
    return f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">{label}</a>'


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
        tr(lang, "detail_picker"),
        options=list(options.keys()),
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
            "".join(citation_chip_html(doc.get("title"), doc.get("url")) for doc in documents),
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


def _screen_knowledge(base_url: str, lang: str) -> None:
    """Screen 4: Knowledge Base (RAG) inspection and ad-hoc retrieval."""
    import streamlit as st

    def _render_knowledge_body() -> None:
        st.markdown(f"### 📚 {tr(lang, 'knowledge_title')}", unsafe_allow_html=True)

        # ── Corpus status ──
        status_code, status_res = _fetch_knowledge_status(base_url)
        if status_code != 200 or not isinstance(status_res, dict):
            st.error(tr(lang, "knowledge_fetch_error", code=status_code))
            return

        corpus_status = str(status_res.get("status", "unavailable"))
        doc_count = int(status_res.get("document_count", 0))
        chunk_count = int(status_res.get("chunk_count", 0))

        status_labels = {
            "ready": tr(lang, "knowledge_ready"),
            "degraded": tr(lang, "knowledge_degraded"),
            "unavailable": tr(lang, "knowledge_unavailable"),
        }
        status_label = status_labels.get(corpus_status, corpus_status)

        col_status, col_docs, col_chunks = st.columns(3)
        with col_status:
            st.metric(tr(lang, "knowledge_status_label"), status_label)
        with col_docs:
            st.metric(tr(lang, "knowledge_doc_count", count=doc_count), doc_count)
        with col_chunks:
            st.metric(tr(lang, "knowledge_chunk_count", count=chunk_count), chunk_count)

        if corpus_status == "unavailable":
            st.warning(tr(lang, "knowledge_empty_corpus"))
            return

        # ── Document list ──
        st.markdown(f"**{tr(lang, 'knowledge_docs_heading')}**")
        docs_code, docs_res = _fetch_knowledge_documents(base_url)
        if docs_code == 200 and isinstance(docs_res, dict):
            documents = _as_list(docs_res.get("documents"))
            if documents:
                rows = [
                    {
                        tr(lang, "knowledge_doc_title"): doc.get("title", "—"),
                        tr(lang, "knowledge_doc_sections"): doc.get("section_count", 0),
                        tr(lang, "knowledge_doc_source"): doc.get("source_url", "—"),
                    }
                    for doc in documents
                ]
                st.table(rows)
            else:
                st.info(tr(lang, "knowledge_empty_corpus"))
        elif docs_code != 200:
            st.error(tr(lang, "knowledge_fetch_error", code=docs_code))

        # ── Ad-hoc query ──
        st.markdown("---")
        query = st.text_input(
            tr(lang, "knowledge_query_button"),
            placeholder=tr(lang, "knowledge_query_placeholder"),
            key="knowledge_query_input",
        )
        top_k = st.slider("Top K", min_value=1, max_value=20, value=5, key="knowledge_top_k")

        if not query or not query.strip():
            return

        search_clicked = st.button(tr(lang, "knowledge_query_button"), key="knowledge_search_btn")
        if not search_clicked:
            return

        with st.spinner(tr(lang, "loading")):
            chat_code, chat_res = _post_knowledge_chat(base_url, query.strip(), top_k)

        if chat_code != 200 or not isinstance(chat_res, dict):
            st.error(tr(lang, "knowledge_fetch_error", code=chat_code))
            return

        chunks = _as_list(chat_res.get("chunks"))
        retrieval_status = str(chat_res.get("retrieval_status", ""))
        latency_ms = chat_res.get("latency_ms", 0)

        st.markdown(f"**{tr(lang, 'knowledge_results_heading')}**")
        st.caption(tr(lang, "knowledge_latency", ms=latency_ms))

        if not chunks or retrieval_status == "no_results":
            st.info(tr(lang, "knowledge_no_results"))
            return

        for i, chunk in enumerate(chunks, 1):
            title = html.escape(str(chunk.get("document_title", "—")))
            section = html.escape(str(chunk.get("section") or "—"))
            text = html.escape(str(chunk.get("text", "")))
            score = chunk.get("relevance_score", 0.0)
            rerank_score = chunk.get("rerank_score")
            source_url = chunk.get("source_url", "")

            has_reranker = rerank_score is not None
            reranker_text = (
                f"{tr(lang, 'knowledge_reranker_label')}: "
                f"{tr(lang, 'knowledge_reranker_on')} ({rerank_score:.3f})"
                if has_reranker
                else f"{tr(lang, 'knowledge_reranker_label')}: {tr(lang, 'knowledge_reranker_off')}"
            )

            with st.expander(f"{i}. {title} — §{section}"):
                chip_html = citation_chip_html(title, source_url)
                st.markdown(chip_html, unsafe_allow_html=True)
                st.markdown(
                    f"**{tr(lang, 'knowledge_score_label')}:** `{score:.4f}` · {reranker_text}"
                )
                st.markdown(text)

    if hasattr(st, "fragment"):

        @st.fragment
        def _knowledge_fragment() -> None:
            _render_knowledge_body()

        _knowledge_fragment()
    else:
        _render_knowledge_body()


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
                    st.markdown(f"**{index}. {subject}**  \n{tr(lang, 'sender_label')}: `{sender}`")

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
                    lang,
                    "audit_retrieval",
                    tasks=summary["retrieval_tasks"],
                    docs=summary["document_count"],
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
