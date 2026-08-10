"""Deterministic policies applied after model extraction."""

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta

from cowork_agent.domain import Priority

DEFAULT_QUERY = "is:unread in:inbox category:primary"


def normalize_query(query: str | None) -> str:
    """Ensure v1 can only inspect unread messages in the inbox.

    The default narrows scans to Gmail's Primary tab; callers may explicitly
    supply another Gmail category for a deliberate non-default scan.
    """
    terms = (query or DEFAULT_QUERY).strip().split()
    lowered = {term.lower() for term in terms}
    if "is:unread" not in lowered:
        terms.append("is:unread")
    if "in:inbox" not in lowered:
        terms.append("in:inbox")
    return " ".join(terms)


def validate_max_emails(value: int) -> int:
    if not 1 <= value <= 500:
        raise ValueError("max_emails must be between 1 and 500")
    return value


def calculate_priority(
    deadline: datetime | None,
    now: datetime,
    *,
    required: bool = True,
    explicit_blocker: bool = False,
    impact: str = "none",
) -> tuple[Priority, str]:
    if deadline is not None:
        remaining = deadline - now
        if remaining <= timedelta(hours=24):
            return Priority.URGENT, "Deadline đã quá hạn hoặc còn không quá 24 giờ."
        if remaining <= timedelta(hours=72):
            return Priority.HIGH, "Deadline còn không quá 72 giờ."
    if impact in {"service_outage", "data_loss_risk", "security_risk"}:
        reasons = {
            "service_outage": "Sự cố đang làm gián đoạn dịch vụ.",
            "data_loss_risk": "Có nguy cơ mất dữ liệu cần xử lý ngay.",
            "security_risk": "Có rủi ro bảo mật cần xử lý ngay.",
        }
        return Priority.URGENT, reasons[impact]
    if impact == "production_blocked":
        return Priority.HIGH, "Sự cố đang chặn build hoặc triển khai production."
    if explicit_blocker:
        return Priority.HIGH, "Yêu cầu được nêu rõ là đang chặn tiến độ."
    if required:
        return Priority.MEDIUM, "Có hành động bắt buộc nhưng chưa có căn cứ khẩn cấp."
    return Priority.LOW, "Hành động tùy chọn và không có deadline."


def action_fingerprint(
    mailbox_connection_id: str,
    thread_id: str,
    title: str,
    deadline: datetime | None,
) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()
    deadline_date = deadline.date().isoformat() if deadline else "none"
    raw = "\x1f".join((mailbox_connection_id, thread_id, normalized, deadline_date))
    return hashlib.sha256(raw.encode()).hexdigest()
