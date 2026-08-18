"""Tests for the bounded Gmail evaluation-candidate export."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cowork_agent.features.email_action_plan.schemas import MessageRef, SearchPage
from tests.unit.scripts.cli_harness import load_script


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


def _envelope(message_id: str, thread_id: str, body: str):
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
        received_at=datetime(2026, 8, 18, tzinfo=UTC),
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


def test_fetch_candidates_paginates_to_the_cap_and_writes_metadata_only(tmp_path: Path) -> None:
    module = load_module()
    first = _envelope("m1", "t1", "first body")
    second = _envelope("m2", "t2", "x" * 500)
    third = _envelope("m3", "t3", "third body")
    mailbox = FakeMailbox(
        [
            SearchPage(
                messages=(MessageRef("m1", "t1"), MessageRef("m2", "t2")),
                next_cursor="page-2",
                estimated_total=3,
            ),
            SearchPage(
                messages=(MessageRef("m3", "t3"),),
                next_cursor=None,
                estimated_total=3,
            ),
        ],
        {"t1": (first,), "t2": (second,), "t3": (third,)},
    )

    candidates = asyncio.run(
        module.fetch_candidates(mailbox, "connection-1", query="is:unread in:inbox", limit=3)
    )

    assert [candidate["gmail_message_id"] for candidate in candidates] == ["m1", "m2", "m3"]
    assert len(candidates[1]["snippet"]) == 350
    assert "normalized_body" not in candidates[1]
    assert mailbox.search_calls == [
        ("connection-1", "is:unread in:inbox", 3, None),
        ("connection-1", "is:unread in:inbox", 1, "page-2"),
    ]

    output = tmp_path / "candidates.json"
    module.write_candidates(candidates, output)
    assert json.loads(output.read_text(encoding="utf-8")) == candidates


def test_limit_is_bounded_to_200() -> None:
    module = load_module()

    assert module.validate_limit(1) == 1
    assert module.validate_limit(200) == 200
    with pytest.raises(ValueError, match="between 1 and 200"):
        module.validate_limit(201)
