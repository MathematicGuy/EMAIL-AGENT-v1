"""Tests for the fresh full-content Gmail evaluation-candidate export."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cowork_agent.features.email_action_plan.schemas import MessageRef, SearchPage
from tests.unit.scripts.cli_harness import load_script

pytestmark = pytest.mark.extended

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
OLD_TIME = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
NEW_TIME = datetime(2026, 8, 19, 11, 0, tzinfo=UTC)


def load_module():
    return load_script("fetch_gmail_evaluation_candidates")


def test_help_runs_without_gmail_credentials() -> None:
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "fetch_gmail_evaluation_candidates.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "--limit" in result.stdout


def _envelope(
    message_id: str,
    thread_id: str,
    body: str,
    *,
    received_at: datetime = NOW,
):
    from cowork_agent.domain.target_contracts import (
        BodyFormat,
        EphemeralEmailEnvelope,
        FetchStatus,
    )

    return EphemeralEmailEnvelope(
        run_id="",
        user_id="",
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        sender_name="Sender",
        sender_email=f"{message_id}@example.com",
        recipients=(),
        subject=f"Subject {message_id}",
        received_at=received_at,
        labels=("INBOX", "UNREAD"),
        normalized_body=body,
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


class FakeMailbox:
    def __init__(self, pages, threads):
        self.pages = iter(pages)
        self.threads = threads
        self.search_calls = []

    async def search_unread(self, connection_id, query, page_size, cursor=None):
        self.search_calls.append((connection_id, query, page_size, cursor))
        return next(self.pages)

    async def get_thread(self, connection_id, thread_id):
        return self.threads[thread_id]


def test_fetch_candidates_keeps_complete_content_and_orders_newest_first() -> None:
    module = load_module()
    older = _envelope("m1", "t1", "old body", received_at=OLD_TIME)
    newer = _envelope("m2", "t2", "x" * 5000, received_at=NEW_TIME)
    mailbox = FakeMailbox(
        [
            SearchPage(
                messages=(MessageRef("m1", "t1"), MessageRef("m2", "t2")),
                next_cursor=None,
                estimated_total=2,
            )
        ],
        {"t1": (older,), "t2": (newer,)},
    )

    dataset = asyncio.run(
        module.fetch_candidates(
            mailbox,
            "connection-1",
            query="in:inbox",
            limit=2,
            fetched_at=NOW,
        )
    )

    assert [case["source_message_id"] for case in dataset["cases"]] == ["m2", "m1"]
    assert dataset["cases"][0]["case_id"] == "email_case_001"
    assert dataset["cases"][1]["case_id"] == "email_case_002"
    assert dataset["cases"][0]["gmail_content"] == "x" * 5000
    assert "snippet" not in dataset["cases"][0]
    assert dataset["case_count"] == 2
    assert dataset["gmail_query"] == "in:inbox"
    assert dataset["ordering"] == "received_at_desc"
    assert dataset["fetched_at"] == NOW.isoformat()


def test_candidate_record_removes_repeated_invisible_format_controls() -> None:
    module = load_module()
    message = _envelope(
        "m1",
        "t1",
        "Readable\u200e\u200f\ufeff\u200b\u200c\u200d content",
    )

    candidate = module._candidate_record(message, 1)

    assert candidate["gmail_content"] == "Readable content"


def test_candidate_record_replaces_line_boundaries_with_spaces() -> None:
    module = load_module()
    message = _envelope(
        "m1",
        "t1",
        "Overview\n  First action  \n\nSecond action\nOpen item [link1]",
    )

    candidate = module._candidate_record(message, 1)

    assert candidate["gmail_content"] == (
        "Overview First action Second action Open item [link1]"
    )


def test_candidate_record_removes_grapheme_artifacts_but_preserves_emoji_zwj() -> None:
    module = load_module()
    message = _envelope("m1", "t1", "A\u200c\u034f\u00adB 👩\u200d💻 می\u200cروم")

    candidate = module._candidate_record(message, 1)

    assert candidate["gmail_content"] == "AB 👩‍💻 می‌روم"


def test_candidate_record_removes_only_whole_separator_lines() -> None:
    module = load_module()
    message = _envelope("m1", "t1", "Keep\n---------\n|----|\n--\n000\n...\nKeep-inline")

    candidate = module._candidate_record(message, 1)

    assert candidate["gmail_content"] == "Keep 000 ... Keep-inline"


def test_fetch_candidates_deduplicates_message_ids_across_pages() -> None:
    module = load_module()
    first = _envelope("m1", "t1", "first body", received_at=OLD_TIME)
    second = _envelope("m2", "t2", "second body", received_at=NEW_TIME)
    mailbox = FakeMailbox(
        [
            SearchPage(
                messages=(MessageRef("m1", "t1"),),
                next_cursor="page-2",
                estimated_total=2,
            ),
            SearchPage(
                messages=(MessageRef("m1", "t1"), MessageRef("m2", "t2")),
                next_cursor=None,
                estimated_total=2,
            ),
        ],
        {"t1": (first,), "t2": (second,)},
    )

    dataset = asyncio.run(
        module.fetch_candidates(
            mailbox,
            "connection-1",
            query="in:inbox",
            limit=2,
            fetched_at=NOW,
        )
    )

    assert [case["source_message_id"] for case in dataset["cases"]] == ["m2", "m1"]
    assert mailbox.search_calls == [
        ("connection-1", "in:inbox", 2, None),
        ("connection-1", "in:inbox", 1, "page-2"),
    ]


def test_write_candidates_validates_before_replacing_existing_destination(tmp_path: Path) -> None:
    module = load_module()
    destination = tmp_path / "candidates.json"
    original = b"original bytes\n"
    destination.write_bytes(original)
    invalid_dataset = {
        "schema_version": 1,
        "fetched_at": NOW.isoformat(),
        "gmail_query": "in:inbox",
        "ordering": "received_at_desc",
        "case_count": 0,
        "cases": [],
    }

    with pytest.raises(ValueError, match="requires exactly 2 cases"):
        module.write_candidates(invalid_dataset, destination, expected_count=2)

    assert destination.read_bytes() == original


def test_limit_is_bounded_to_200() -> None:
    module = load_module()

    assert module.validate_limit(1) == 1
    assert module.validate_limit(200) == 200
    with pytest.raises(ValueError, match="between 1 and 200"):
        module.validate_limit(201)
