"""Automated checks for the demo GUI (SPEC-Demo-Frontend; closes the
documented §15-gate gap of zero GUI tests). Pure presentation helpers are
unit-tested here; live behavior is proven per SPEC §9."""

from typing import Any

from cowork_agent.gui import app as gui

SAMPLE_TASK: dict[str, Any] = {
    "task_id": "t1",
    "run_id": "r1",
    "gmail_message_id": "m1",
    "gmail_url": "https://mail.google.com/mail/u/0#all/xyz",
    "source_message_ids": ["m1", "m2"],
    "incident_key": None,
    "title": "Gia hạn hợp đồng <script>alert(1)</script>",
    "request_summary": "Ký lại hợp đồng trước hạn",
    "actionability": "action_required",
    "route": "retrieve_rag",
    "priority": "urgent",
    "deadline": "2026-08-15T17:00:00+00:00",
    "action_plan": [{"step": 1, "instruction": "Đọc quy trình", "supporting_citation_ids": ["c1"]}],
    "supporting_documents": [
        {
            "citation_id": "c1",
            "document_id": "d1",
            "title": "Quy trình & <b>hướng dẫn</b>",
            "section": None,
            "url": "https://docs.example.com/x",
            "relevance_score": 0.9,
        }
    ],
    "missing_information": ["Thiếu ngày bắt đầu"],
    "classifier_confidence": 0.92,
    "generation_confidence": 0.8,
    "validation_status": "system_generated",
    "created_at": "2026-08-08T00:00:00+00:00",
}


def test_module_is_importable_and_main_is_guarded() -> None:
    assert callable(gui.main)
    assert set(gui.SUPPORTED_LANGUAGES) == {"vi", "en"}


def test_string_catalogs_are_parallel_across_languages() -> None:
    vi_keys = set(gui.STRINGS["vi"])
    for lang, catalog in gui.STRINGS.items():
        assert set(catalog) == vi_keys, f"catalog drift in {lang}"
    for kind, values in gui.ENUM_LABELS.items():
        for value, labels in values.items():
            assert set(labels) == {"vi", "en"}, f"enum label drift: {kind}.{value}"


def test_tr_formats_placeholders() -> None:
    assert gui.tr("en", "tasks_found", count=3) == (
        "🎉 Found and extracted **3 tasks** from unread emails!"
    )
    assert gui.tr("vi", "correlated", count=2) == "Tổng hợp từ 2 email"


def test_citation_chip_escapes_title_and_restricts_scheme() -> None:
    safe = gui.citation_chip_html("Doc <b>", "https://example.com/?a=1&b=2")
    assert 'href="https://example.com/?a=1&amp;b=2"' in safe
    assert "Doc &lt;b&gt;" in safe
    assert "javascript:alert(1)" not in gui.citation_chip_html("X", "javascript:alert(1)")
    assert "<a" not in gui.citation_chip_html("X", "javascript:alert(1)")


def test_build_task_card_escapes_llm_text_and_marks_partial_plan() -> None:
    card = gui.build_task_card_html(SAMPLE_TASK, 1, "en")
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "priority-urgent" in card
    assert "partial-plan" in card
    assert "PARTIAL PLAN" in card
    assert "Correlated from 2 emails" in card
    assert "92%" in card


def test_build_task_card_handles_missing_priority_and_single_source() -> None:
    task = dict(SAMPLE_TASK)
    task["priority"] = None
    task["missing_information"] = []
    task["source_message_ids"] = ["m1"]
    card = gui.build_task_card_html(task, 2, "vi")
    assert "priority-unknown" in card
    assert "partial-plan" not in card
    assert "Tổng hợp từ" not in card


def test_route_summary_counts_routes_retrieval_and_partial_plans() -> None:
    second = dict(SAMPLE_TASK)
    second["task_id"] = "t2"
    second["route"] = "direct_plan"
    second["missing_information"] = []
    second["supporting_documents"] = []
    summary = gui.route_summary([SAMPLE_TASK, second])
    assert summary["routes"] == {"retrieve_rag": 1, "direct_plan": 1}
    assert summary["retrieval_tasks"] == 1
    assert summary["document_count"] == 1
    assert summary["partial_plans"] == 1
    assert summary["total"] == 2


def test_format_deadline_and_confidence_fallbacks() -> None:
    assert gui.format_deadline("2026-08-15T17:00:00+00:00", "en") == "2026-08-15 17:00"
    assert gui.format_deadline(None, "en") == "Unknown"
    assert gui.format_confidence(0.92) == "92%"
    assert gui.format_confidence("n/a") == "?"


def test_safe_url_allows_only_http_schemes() -> None:
    assert gui.safe_url("https://mail.google.com/x") == "https://mail.google.com/x"
    assert gui.safe_url("http://example.com") == "http://example.com"
    assert gui.safe_url("javascript:alert(1)") is None
    assert gui.safe_url(None) is None


def test_filter_tasks_by_priority() -> None:
    t_urgent = dict(SAMPLE_TASK, task_id="t1", priority="urgent")
    t_low = dict(SAMPLE_TASK, task_id="t2", priority="low")
    tasks = [t_urgent, t_low]

    assert len(gui.filter_tasks(tasks, "all")) == 2
    assert len(gui.filter_tasks(tasks, "urgent")) == 1
    assert gui.filter_tasks(tasks, "urgent")[0]["task_id"] == "t1"
    assert len(gui.filter_tasks(tasks, "low")) == 1
    assert gui.filter_tasks(tasks, "low")[0]["task_id"] == "t2"
    assert len(gui.filter_tasks(tasks, "high")) == 0


def test_memory_badges_html_labels_each_kind_and_counts_repeats() -> None:
    markup = gui.memory_badges_html(
        [("semantic", "a"), ("semantic", "b"), ("declarative", "c")], "en"
    )
    assert "Company knowledge ×2" in markup
    assert "Personal profile" in markup
    assert markup.count("<span") == 2


def test_memory_badges_html_never_renders_the_opaque_source_id() -> None:
    markup = gui.memory_badges_html([("episodic", "episode-secret-id")], "en")
    assert "episode-secret-id" not in markup
    assert "Approved episode" in markup


def test_memory_badges_html_drops_unknown_memory_kinds() -> None:
    assert gui.memory_badges_html([("raw_email", "m1")], "en") == ""
    assert gui.memory_badges_html([], "vi") == ""


def test_chat_error_text_prefers_backend_safe_message() -> None:
    assert gui.chat_error_text("http_500", "Try again shortly.", "en") == "Try again shortly."


def test_chat_error_text_localizes_client_side_codes() -> None:
    assert gui.chat_error_text("stream_unavailable", None, "en") == (
        "Lost the connection to the backend chat stream."
    )
    assert gui.chat_error_text("http_503", "  ", "en") == "The backend rejected this chat turn."
    assert gui.chat_error_text(None, None, "en") == gui.tr("en", "error_unknown")


def test_missing_increment_b_endpoints_are_declared_not_mocked() -> None:
    assert gui.MISSING_INCREMENT_B_ENDPOINTS
    assert all(isinstance(endpoint, str) for endpoint in gui.MISSING_INCREMENT_B_ENDPOINTS)


def test_sort_tasks_by_priority_and_deadline() -> None:
    t_low = dict(SAMPLE_TASK, task_id="t1", priority="low", deadline="2026-08-20")
    t_urgent = dict(SAMPLE_TASK, task_id="t2", priority="urgent", deadline="2026-08-10")
    t_high = dict(SAMPLE_TASK, task_id="t3", priority="high", deadline="2026-08-15")
    tasks = [t_low, t_urgent, t_high]

    sorted_desc = gui.sort_tasks(tasks, "priority_desc")
    assert [t["task_id"] for t in sorted_desc] == ["t2", "t3", "t1"]

    sorted_asc = gui.sort_tasks(tasks, "priority_asc")
    assert [t["task_id"] for t in sorted_asc] == ["t1", "t3", "t2"]

    sorted_deadline = gui.sort_tasks(tasks, "deadline")
    assert [t["task_id"] for t in sorted_deadline] == ["t2", "t3", "t1"]

    sorted_default = gui.sort_tasks(tasks, "default")
    assert [t["task_id"] for t in sorted_default] == ["t1", "t2", "t3"]
