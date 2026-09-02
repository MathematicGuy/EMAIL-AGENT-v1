"""Replay the episodes a live run actually wrote, through the real read path.

The seeded data is correct - the revision is approved, eligible, and links the
passport create - and the run still graded `stale`, so the fault is downstream of
the write. This uses the captured payloads rather than hand-written ones, and the
eval's two-session shape: seeding writes into `<session>-seed`, the probe asks
from `<session>`.

No model, no network.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    EpisodeTransition,
    ChatMessageRequest,
    MemoryContextRequest,
    MemoryNamespace,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.domain.target_contracts import ValidationStatus
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.retrieval_policy import select_memory_reads
from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository

QUESTION = "Ngày nộp hồ sơ hộ chiếu trên tác vụ trước là ngày nào?"
PAYLOADS = json.load(open("scratchpad/real_seeded_episodes.json", encoding="utf-8"))
SEED_SESSION = PAYLOADS[0]["chat_session_id"]
NOW = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)


class _Buffer:
    def read(self, namespace: object) -> tuple[()]:
        del namespace
        return ()


def _namespace(scope: ChatMemoryScope, payload: dict) -> MemoryNamespace:
    return MemoryNamespace(
        scope=scope,
        memory_type=MemoryType.EPISODIC,
        record_id=payload["record_id"],
        source_id=payload["chat_turn_id"],
    )


async def main() -> None:
    repo = SQLiteChatRepository(Path(tempfile.mkdtemp()) / "real.db")
    await repo.initialize()
    user_id = PAYLOADS[0]["user_id"]
    seed_scope = ChatMemoryScope(tenant_id="t", user_id=user_id, session_id=SEED_SESSION)
    for payload in PAYLOADS:
        # The store only accepts an episode in its pre-approval shape, so the
        # captured approved row is written back the way the product wrote it.
        pending = dict(payload, validation_status="system_generated", retrieval_eligible=False)
        episode = TaskEpisode.from_dict(pending)
        await repo.write_task_episode(
            _namespace(seed_scope, payload), episode, expires_at=NOW + timedelta(days=30)
        )
        await repo.transition_task_episode(
            EpisodeTransition(
                namespace=_namespace(seed_scope, payload),
                episode_id=episode.episode_id,
                from_status=ValidationStatus.SYSTEM_GENERATED,
                to_status=ValidationStatus.USER_APPROVED,
                retrieval_eligible=True,
                transitioned_at=NOW + timedelta(seconds=60),
            ),
        )
        print(f"  wrote {episode.episode_id[:8]} eligible={episode.retrieval_eligible} "
              f"sup={str(episode.supersedes)[:8]}")

    for label, session in (("seed session", SEED_SESSION), ("probe session", "probe-1")):
        scope = ChatMemoryScope(tenant_id="t", user_id=user_id, session_id=session)
        gateway = MemoryGateway(scope=scope, session_buffer=_Buffer(), episodic_memory=repo)
        message = ChatMessageRequest(session, QUESTION, "idem-1")
        response = await gateway.read_context(
            MemoryContextRequest(session_id=session, scope=scope, reads=select_memory_reads(message))
        )
        print(f"\n{label}: {len(response.episodes)} episode(s)")
        for episode in response.episodes:
            print(f"   {episode.episode_id[:8]} sup={str(episode.supersedes)[:8]:8} {episode.task_title}")


asyncio.run(main())
