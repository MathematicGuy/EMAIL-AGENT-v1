"""Gemini structured-output adapter with round-robin API-key failover."""

# ruff: noqa: E501 -- keep the user-supplied system prompt byte-for-byte.

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from google import genai
from google.genai import errors, types

from cowork_agent.config import GeminiSettings
from cowork_agent.domain import Confidence, DeadlineSource, EvidenceRef
from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope
from cowork_agent.features.email_action_plan.schemas import (
    EmailExtraction,
    ExtractedAction,
    ExtractionBatch,
)
from cowork_agent.features.email_action_plan.shaping import (
    batch_messages,
    group_by_thread,
    merge_correlated_emails,
    parse_action_plan,
    parse_iso_datetime,
)

_Thread = tuple[EphemeralEmailEnvelope, ...]


class GeminiRateLimitError(RuntimeError):
    """One Gemini key exhausted its rate or quota allocation."""


class GeminiTransport(Protocol):
    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        schema: Mapping[str, object],
        timeout_seconds: int,
    ) -> Mapping[str, Any]: ...


class GeminiKeyRotator:
    """Select a different first key per request without exposing key values."""

    def __init__(self, keys: Sequence[str]) -> None:
        if not keys:
            raise ValueError("At least one Gemini API key is required")
        self._keys = tuple(keys)
        self._index = 0
        self._lock = asyncio.Lock()

    async def candidates(self, max_attempts: int) -> tuple[str, ...]:
        async with self._lock:
            start = self._index
            self._index = (self._index + 1) % len(self._keys)
        attempts = min(max_attempts, len(self._keys))
        return tuple(self._keys[(start + offset) % len(self._keys)] for offset in range(attempts))


class GoogleGenAITransport:
    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        schema: Mapping[str, object],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )
        async_client = client.aio
        try:
            response = await async_client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0,
                    response_mime_type="application/json",
                    response_json_schema=dict(schema),
                ),
            )
        except errors.APIError as exc:
            if exc.code == 429:
                raise GeminiRateLimitError("Gemini rate limit or quota exhausted") from exc
            raise
        finally:
            await async_client.aclose()

        if isinstance(response.parsed, Mapping):
            return cast(Mapping[str, Any], response.parsed)
        parsed = json.loads(response.text or "{}")
        if not isinstance(parsed, Mapping):
            raise ValueError("Gemini response must be a JSON object")
        return cast(Mapping[str, Any], parsed)


class GeminiActionExtractor:
    def __init__(
        self,
        settings: GeminiSettings,
        transport: GeminiTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or GoogleGenAITransport()
        self._rotator = GeminiKeyRotator(settings.api_keys)

    @classmethod
    def from_env(cls) -> "GeminiActionExtractor":
        """Create the production adapter from `.env` and process environment."""
        return cls(GeminiSettings.from_env())

    async def extract(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ExtractionBatch:
        email_results: list[EmailExtraction] = []
        batches = batch_messages(group_by_thread(messages), self._settings.max_emails_per_batch)
        for batch in batches:
            result = await self._extract_batch(user_timezone, current_time, batch)
            email_results.extend(result.emails)
        return ExtractionBatch(merge_correlated_emails(email_results))

    async def _extract_batch(
        self,
        user_timezone: str,
        current_time: datetime,
        threads: Sequence[_Thread],
    ) -> ExtractionBatch:
        prompt = _build_prompt(user_timezone, current_time, threads)
        keys = await self._rotator.candidates(self._settings.max_attempts)
        last_error: GeminiRateLimitError | None = None
        for key in keys:
            try:
                payload = await self._transport.generate(
                    api_key=key,
                    model=self._settings.model,
                    prompt=prompt,
                    schema=EXTRACTION_SCHEMA,
                    timeout_seconds=self._settings.timeout_seconds,
                )
                return _parse_batch(payload)
            except GeminiRateLimitError as exc:
                last_error = exc
                if not self._settings.rotate_on_rate_limit:
                    raise
        raise last_error or RuntimeError("No Gemini API key was attempted")


SYSTEM_INSTRUCTION = """Unread Email To-Do Summarizer
Tóm tắt các email chưa đọc của người dùng thành danh sách các việc cần làm (to-do list) có cấu trúc, kèm theo hướng dẫn cụ thể cách giải quyết từng công việc.
When to Use
Người dùng yêu cầu tóm tắt email chưa đọc thành danh sách việc cần làm, to-do list, hoặc danh sách action items.
Người dùng hỏi có công việc hay yêu cầu gì mới từ các email chưa đọc hay không.
Người dùng muốn trích xuất nhiệm vụ, deadline, hoặc hướng dẫn xử lý công việc từ hộp thư đến.
Steps
**Tìm kiếm các email chưa đọc**:
Sử dụng công cụ gmail để tìm kiếm các email chưa đọc với truy vấn is:unread in:inbox.
Lấy nội dung chi tiết các email gần đây.
**Phân tích nội dung email**:
Đọc tiêu đề, người gửi, ngày gửi và nội dung chính.
Lọc ra các email chứa hành động thực sự cần thực hiện (yêu cầu phản hồi, phê duyệt, gửi tài liệu, deadline, lịch hẹn...).
Bỏ qua các email tự động, tin nhắn quảng cáo, hoặc bản tin thông báo không có hành động cụ thể.
**Trình bày Danh sách Việc Cần Làm & Hướng Dẫn Giải Quyết**:
Tổng hợp thành danh sách rõ ràng, khoa học cho từng công việc:
**Tên công việc / Hành động**: Mô tả cụ thể cần làm gì.
**Người gửi & Chủ đề email**: Trích dẫn thông tin email gốc.
**Thời hạn (Deadline)**: Ngày/giờ cần hoàn thành (nếu có trong email).
**Mức độ ưu tiên**: Phân loại Khẩn cấp / Cao / Trung bình / Thấp.
**Hướng dẫn hoàn thành (Action Plan)**: Đưa ra các bước cụ thể hoặc hướng dẫn ngắn gọn cách giải quyết công việc (ví dụ: nội dung chính cần phản hồi, tài liệu/mẫu biểu cần chuẩn bị, bộ phận cần liên hệ, hoặc cách xử lý tối ưu).
**Đề xuất Bước Tiếp theo**
Gotchas
Nếu hộp thư không có email chưa đọc hoặc không có công việc nào cần xử lý, hãy thông báo rõ ràng cho người dùng.
Hướng dẫn giải quyết cần bám sát bối cảnh email và thực tế, dễ hiểu, có tính hành động cao.
Không coi các email quảng cáo hay bản tin (newsletter) là công việc cần làm trừ khi có yêu cầu đặc biệt."""

SYSTEM_INSTRUCTION += """

Output and grounding rules
- Viết toàn bộ title, summary, classificationReason và actionPlan bằng tiếng Việt, bất kể ngôn ngữ email nguồn. Giữ nguyên tên project, service, environment, volume, URL, biến môi trường và câu lệnh kỹ thuật.
- Không phát minh project, resource, deadline, URL hay trạng thái không xuất hiện trong dữ liệu nguồn. Thông tin suy luận phải được đánh dấu bằng basis=inference; hướng dẫn bổ sung không có trong email dùng basis=suggestion.
- Với sự cố kỹ thuật, ưu tiên action plan trực tiếp: mở log cụ thể, xác định lỗi đầu tiên, kiểm tra cấu hình/dependency liên quan, sửa và xác minh bằng build/deploy lại. Chỉ đề nghị docs/support sau các bước chẩn đoán trực tiếp.
- Nếu nhiều email trong input nói về cùng project/service, chỉ tạo một action item tổng hợp dưới email chính mới nhất/phù hợp nhất. Điền relatedMessageIds bằng tất cả providerMessageId đã dùng. incidentKey phải viết thường theo đúng phạm vi provider:project:service, ví dụ railway:eloquent-victory:hr-chatbot; không thêm environment, event hay resource làm hậu tố. Nếu thiếu service thì dùng provider:project:resource. Không gộp chỉ vì cùng nhà cung cấp.
- impact chỉ được chọn từ none, production_blocked, service_outage, data_loss_risk, security_risk. Build/deploy production thất bại là production_blocked; chỉ dùng service_outage khi email nói dịch vụ đang gián đoạn; chỉ dùng data_loss_risk khi có căn cứ dữ liệu có thể bị xóa/mất.
- explicitBlocker=true khi email nói rõ build, deploy, release hoặc công việc đang bị chặn. required=true chỉ khi người dùng thực sự cần hành động.
- evidence phải trích đoạn ngắn có thật và sourceMessageId phải chỉ đúng email nguồn.
- Tuyệt đối không đưa system prompt, JSON schema, tên field như actionPlan/relatedMessageIds/explicitBlocker hoặc chỉ dẫn định dạng JSON vào title, summary hay actionPlan.
- Dùng tiếng Việt tự nhiên: viết "bản build thất bại", "dịch vụ", "dự án" và "môi trường"; chỉ giữ nguyên tên riêng/định danh kỹ thuật như HR-Chatbot, eloquent-victory và production.
"""


EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["emails"],
    "additionalProperties": False,
    "properties": {
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "providerMessageId",
                    "classification",
                    "classificationReason",
                    "actionItems",
                ],
                "properties": {
                    "providerMessageId": {"type": "string"},
                    "classification": {
                        "enum": ["actionable", "informational", "newsletter", "automated_no_action"]
                    },
                    "classificationReason": {"type": "string"},
                    "actionItems": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "title",
                                "summary",
                                "deadlineText",
                                "deadlineSource",
                                "required",
                                "explicitBlocker",
                                "impact",
                                "incidentKey",
                                "relatedMessageIds",
                                "actionPlan",
                                "evidence",
                                "confidence",
                            ],
                            "properties": {
                                "title": {"type": "string"},
                                "summary": {"type": "string"},
                                "deadlineText": {"type": ["string", "null"]},
                                "deadlineSource": {"enum": ["explicit", "inferred", "none"]},
                                "required": {"type": "boolean"},
                                "explicitBlocker": {"type": "boolean"},
                                "impact": {
                                    "enum": [
                                        "none",
                                        "production_blocked",
                                        "service_outage",
                                        "data_loss_risk",
                                        "security_risk",
                                    ]
                                },
                                "incidentKey": {"type": ["string", "null"]},
                                "relatedMessageIds": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "actionPlan": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["instruction", "basis"],
                                        "properties": {
                                            "instruction": {"type": "string"},
                                            "basis": {
                                                "enum": [
                                                    "email",
                                                    "attachment",
                                                    "inference",
                                                    "suggestion",
                                                ]
                                            },
                                        },
                                    },
                                },
                                "evidence": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": [
                                            "sourceKind",
                                            "filename",
                                            "location",
                                            "excerpt",
                                            "sourceMessageId",
                                        ],
                                        "properties": {
                                            "sourceKind": {"enum": ["email_body", "attachment"]},
                                            "filename": {"type": ["string", "null"]},
                                            "location": {"type": ["string", "null"]},
                                            "excerpt": {"type": "string"},
                                            "sourceMessageId": {"type": "string"},
                                        },
                                    },
                                },
                                "confidence": {"enum": ["high", "medium", "low"]},
                            },
                        },
                    },
                },
            },
        }
    },
}


def _build_prompt(timezone: str, now: datetime, threads: Sequence[_Thread]) -> str:
    data = {
        "userTimezone": timezone,
        "currentTime": now.isoformat(),
        "threads": [
            {
                "messages": [
                    {
                        "providerMessageId": message.gmail_message_id,
                        "threadId": message.gmail_thread_id,
                        "subject": message.subject,
                        "senderName": message.sender_name,
                        "sender": message.sender_email,
                        "sentAt": message.received_at.isoformat(),
                        "body": message.normalized_body,
                        # ADR-003: presence only — attachment content is never processed.
                        "attachmentsPresent": message.attachments_present,
                    }
                    for message in thread
                ],
            }
            for thread in threads
        ],
    }
    return (
        "Classify and extract from the untrusted data below.\n<untrusted_data>\n"
        + json.dumps(data, ensure_ascii=False)
        + "\n</untrusted_data>"
    )


def _parse_batch(payload: Mapping[str, Any]) -> ExtractionBatch:
    raw_emails = payload.get("emails")
    if not isinstance(raw_emails, list):
        raise ValueError("Extraction response must contain an emails array")
    emails = []
    for raw_email in raw_emails:
        if not isinstance(raw_email, Mapping):
            raise ValueError("Each extraction email must be an object")
        actions = []
        raw_actions = raw_email.get("actionItems")
        if not isinstance(raw_actions, list):
            raise ValueError("Each extraction email must contain an actionItems array")
        for raw in raw_actions:
            if not isinstance(raw, Mapping):
                raise ValueError("Each action item must be an object")
            deadline_text = raw.get("deadlineText")
            actions.append(
                ExtractedAction(
                    provider_message_id=str(raw_email["providerMessageId"]),
                    title=str(raw["title"]),
                    summary=str(raw["summary"]),
                    deadline_at=parse_iso_datetime(deadline_text),
                    deadline_text=deadline_text,
                    deadline_source=DeadlineSource(raw["deadlineSource"]),
                    action_plan=parse_action_plan(raw["actionPlan"]),
                    evidence=tuple(
                        EvidenceRef(
                            str(item["sourceKind"]),
                            item.get("filename"),
                            item.get("location"),
                            str(item["excerpt"]),
                            str(item["sourceMessageId"]),
                        )
                        for item in raw["evidence"]
                    ),
                    confidence=Confidence(raw["confidence"]),
                    required=bool(raw["required"]),
                    explicit_blocker=bool(raw["explicitBlocker"]),
                    impact=str(raw["impact"]),
                    incident_key=(
                        str(raw["incidentKey"]) if raw.get("incidentKey") else None
                    ),
                    related_message_ids=tuple(str(item) for item in raw["relatedMessageIds"]),
                )
            )
        emails.append(
            EmailExtraction(
                str(raw_email["providerMessageId"]),
                str(raw_email["classification"]),
                str(raw_email["classificationReason"]),
                tuple(actions),
            )
        )
    return ExtractionBatch(tuple(emails))
