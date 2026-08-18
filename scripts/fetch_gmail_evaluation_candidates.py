"""Export bounded Gmail metadata for human intent-router labeling.

The export deliberately contains the same metadata shape used by the existing
email golden set, but no full message bodies or attachments. Retrieved body
text is used only in memory to create a short evaluation snippet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from email.utils import formataddr
from pathlib import Path
from typing import Any, Protocol

from cowork_agent.features.email_action_plan.schemas import SearchPage

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "evaluations" / "EMAIL" / "gmail_candidates.json"
DEFAULT_QUERY = "is:unread in:inbox"
MAX_LIMIT = 200
SNIPPET_LENGTH = 350


class MailboxReader(Protocol):
    async def search_unread(
        self, connection_id: str, query: str, page_size: int, cursor: str | None = None
    ) -> SearchPage: ...

    async def get_thread(self, connection_id: str, thread_id: str) -> Sequence[Any]: ...


def validate_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


async def fetch_candidates(
    mailbox: MailboxReader,
    connection_id: str,
    *,
    query: str,
    limit: int,
) -> list[dict[str, object]]:
    """Fetch at most ``limit`` matching message IDs, following Gmail pages."""

    validate_limit(limit)
    references: list[Any] = []
    seen_message_ids: set[str] = set()
    cursor: str | None = None

    while len(references) < limit:
        page_size = min(500, limit - len(references))
        page = await mailbox.search_unread(connection_id, query, page_size, cursor)
        for reference in page.messages:
            message_id = str(reference.message_id)
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            references.append(reference)
            if len(references) >= limit:
                break
        if len(references) >= limit or not page.next_cursor:
            break
        cursor = page.next_cursor

    thread_cache: dict[str, Sequence[Any]] = {}
    candidates: list[dict[str, object]] = []
    for reference in references:
        thread_id = str(reference.thread_id)
        if thread_id not in thread_cache:
            thread_cache[thread_id] = await mailbox.get_thread(connection_id, thread_id)
        message_id = str(reference.message_id)
        message = next(
            (item for item in thread_cache[thread_id] if str(item.gmail_message_id) == message_id),
            None,
        )
        if message is None:
            continue
        candidates.append(_candidate_record(message, len(candidates) + 1))
    return candidates


def _candidate_record(message: Any, sequence: int) -> dict[str, object]:
    body = " ".join(str(message.normalized_body).split())
    sender = formataddr((str(message.sender_name), str(message.sender_email))).strip()
    if not sender:
        sender = str(message.sender_email)
    return {
        "id": f"email_candidate_{sequence:03d}",
        "gmail_message_id": str(message.gmail_message_id),
        "gmail_thread_id": str(message.gmail_thread_id),
        "sender": sender,
        "subject": str(message.subject),
        "received_at": message.received_at.isoformat(),
        "labels": [str(label) for label in message.labels],
        "snippet": body[:SNIPPET_LENGTH],
    }


def write_candidates(candidates: Sequence[Mapping[str, object]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(list(candidates), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


async def _load_connection(connection_id: str | None) -> tuple[Any, Any, Any]:
    from cowork_agent.config import GmailSettings
    from cowork_agent.persistence.repositories.mailbox_connections import (
        SQLiteMailboxConnectionRepository,
    )

    settings = GmailSettings.from_env()
    repository = SQLiteMailboxConnectionRepository(settings.connection_db_path)
    connections = tuple(
        item
        for item in await repository.list_all()
        if item.provider == "gmail" and item.status == "active"
    )
    if connection_id is not None:
        connection = next((item for item in connections if item.id == connection_id), None)
        if connection is None:
            raise ValueError(f"active Gmail connection not found: {connection_id}")
        return settings, repository, connection
    if len(connections) != 1:
        raise ValueError(
            "expected exactly one active Gmail connection; pass --connection-id "
            "when there are multiple"
        )
    return settings, repository, connections[0]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export up to 200 unread inbox Gmail messages for intent-router labeling."
    )
    parser.add_argument("--limit", type=int, default=MAX_LIMIT)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--connection-id")
    args = parser.parse_args(argv)

    try:
        validate_limit(args.limit)
        settings, repository, connection = asyncio.run(_load_connection(args.connection_id))
        from cowork_agent.integrations.gmail.auth import TokenCipher
        from cowork_agent.integrations.gmail.provider import GmailMailboxAdapter

        mailbox = GmailMailboxAdapter(
            settings, repository, TokenCipher(settings.token_encryption_key)
        )
        candidates = asyncio.run(
            fetch_candidates(
                mailbox,
                connection.id,
                query=args.query,
                limit=args.limit,
            )
        )
        write_candidates(candidates, args.output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Gmail candidate export failed: {exc}", file=sys.stderr)
        return 2

    print(f"Exported {len(candidates)} Gmail candidates to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
