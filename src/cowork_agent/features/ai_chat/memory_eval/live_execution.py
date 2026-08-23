"""Live memory-evaluation shard execution and whole-run report assembly."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cowork_agent.domain.chat_contracts import MemoryType

from ..ports import ChatReplyPort
from .arms import Arm
from .default_project import NullDefaultProjectEpisodes
from .live_controller import AdapterSet
from .live_env import LiveEnvironment, unavailable_scopes
from .probes import Probe, ProbeSet
from .report import ProbeRow, build_report
from .runner import run_key, run_probe_rows
from .scoring import score


# These are resolved only for a real live run. Importing live_runner at module
# import time pulls the production chat stack into unit tests that only exercise
# report or cleanup ownership.
class _UnloadedProviderFailure(RuntimeError):
    """Placeholder until the real live runner is required."""


LiveSession: Any = None
ask_live: Any = None
build_identity: Any = None
teardown: Any = None
ExcessiveSeedFailuresError: Any = _UnloadedProviderFailure


@dataclass(frozen=True, slots=True)
class MemoryShardResult:
    """The mergeable metadata and private evidence produced by one live shard."""

    rows: tuple[ProbeRow, ...]
    seed_failure_ids: tuple[str, ...]
    private_transcript: tuple[dict[str, object], ...] = field(repr=False)
    nonce: str
    provider_findings: tuple[str, ...]
    scratch_removed: bool


def _load_live_runner() -> None:
    global ExcessiveSeedFailuresError, LiveSession, ask_live, build_identity, teardown
    if LiveSession is not None:
        return
    from . import live_runner

    ExcessiveSeedFailuresError = live_runner.ExcessiveSeedFailuresError
    LiveSession = live_runner.LiveSession
    ask_live = live_runner.ask_live
    build_identity = live_runner.build_identity
    teardown = live_runner.teardown


async def _build_adapters(
    env: LiveEnvironment, probe_set: ProbeSet
) -> tuple[AdapterSet, list[str], Any]:
    """Build the adapters this environment supports, retaining partial findings."""

    failures: list[str] = []
    declarative: object | None = None
    episodic: object | None = None
    pool: Any = None
    if env.postgres_url is not None:
        from psycopg_pool import AsyncConnectionPool

        from cowork_agent.persistence.migrate import apply_migrations
        from cowork_agent.persistence.repositories.postgres import (
            PostgresChatProfileRepository,
            PostgresTaskEpisodeRepository,
        )

        pool = AsyncConnectionPool(env.postgres_url, min_size=1, max_size=4, open=False)
        await pool.open(wait=True)
        await apply_migrations(pool)
        declarative = PostgresChatProfileRepository(pool)
        episodic = NullDefaultProjectEpisodes(PostgresTaskEpisodeRepository(pool))
        print("[memeval] Initialized PostgreSQL repositories", file=sys.stderr, flush=True)
    elif env.sqlite_path is not None:
        from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository

        env.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite_chat = SQLiteChatRepository(env.sqlite_path)
        await sqlite_chat.initialize()
        declarative = sqlite_chat
        episodic = sqlite_chat
        print(
            f"[memeval] Initialized SQLite repository at {env.sqlite_path}",
            file=sys.stderr,
            flush=True,
        )

    semantic: object | None = None
    if env.embeddings_ready:
        from cowork_agent.integrations.rag.bootstrap import build_document_embedder

        from .live_seeding import seed_semantic

        print("[memeval] Seeding semantic memory corpus...", file=sys.stderr, flush=True)
        embedder, _dimensions = build_document_embedder()
        outcome, adapter = await seed_semantic(probe_set.seed, embedder, corpus_root=Path("."))
        if outcome.ok:
            semantic = adapter
            print("[memeval] Semantic memory seeded successfully", file=sys.stderr, flush=True)
        else:
            failures.append(f"semantic: {outcome.reason}")
            print(
                f"[memeval] Semantic memory seeding failed: {outcome.reason}",
                file=sys.stderr,
                flush=True,
            )

    return AdapterSet(declarative, episodic, semantic), failures, pool


def _attach_stream_errors(session: Any, recorded: list[dict[str, object]]) -> None:
    for record in recorded:
        record["stream_errors"] = [
            item
            for item in session.ask_errors
            if item["probe"] == record["probe"] and item["arm"] == record["arm"]
        ]


def _is_scratch_sqlite_path(path: Path | None) -> bool:
    """Only remove files bearing the harness's explicit scratch-file prefix."""

    return path is not None and path.suffix == ".db" and path.name.startswith("memeval-")


def _remove_scratch_sqlite(path: Path | None) -> bool:
    if not _is_scratch_sqlite_path(path):
        return False
    assert path is not None
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


async def execute_memory_shard(
    probe_set: ProbeSet,
    environment: LiveEnvironment,
    reply: ChatReplyPort,
    *,
    provider: str,
    model: str,
    max_consecutive_provider_failures: int = 3,
) -> MemoryShardResult:
    """Execute one live shard, retaining private transcript evidence for its caller."""

    _load_live_runner()
    total_calls = len(probe_set.probes) * 3
    print(
        f"[memeval] Starting evaluation run: probe_set={probe_set.probe_set_id} "
        f"({len(probe_set.probes)} probes, {total_calls} calls) | "
        f"provider={provider} | model={model}",
        file=sys.stderr,
        flush=True,
    )
    identity = build_identity(probe_set, model)
    adapters, findings, pool = await _build_adapters(environment, probe_set)
    findings.extend(item.reason for item in unavailable_scopes(environment))
    session = LiveSession(
        identity=identity,
        adapters=adapters,
        reply=reply,
        seed=probe_set.seed,
        max_consecutive_provider_failures=max_consecutive_provider_failures,
    )
    recorded: list[dict[str, object]] = []
    call_index = 0
    rows: tuple[ProbeRow, ...] = ()

    async def ask(probe: Probe, arm: Arm, masked: MemoryType | None) -> tuple[str, int]:
        nonlocal call_index
        call_index += 1
        print(
            f"[{call_index:02d}/{total_calls:02d}] Asking probe '{probe.probe_id}' "
            f"(target: {probe.targets.value}, arm: {arm.value})...",
            file=sys.stderr,
            flush=True,
        )
        text, latency_ms = await ask_live(session, probe, arm, masked)
        result = score(text, probe)
        certainty = "certain" if result.certain else "uncertain"
        print(
            f"[{call_index:02d}/{total_calls:02d}] Done probe '{probe.probe_id}' "
            f"[{arm.value}] -> outcome: {result.outcome.value} ({certainty}) [{latency_ms}ms]",
            file=sys.stderr,
            flush=True,
        )
        recorded.append(
            {
                "probe": probe.probe_id,
                "targets": probe.targets.value,
                "arm": arm.value,
                "masked": None if masked is None else masked.value,
                "question": probe.question,
                "reply": text,
                "outcome": result.outcome.value,
                "certain": result.certain,
                "why": result.why,
                "latency_ms": latency_ms,
            }
        )
        return text, latency_ms

    try:
        rows = await run_probe_rows(probe_set, ask)
    except ExcessiveSeedFailuresError as error:
        if not recorded:
            raise
        print(f"ERROR: {error}", file=sys.stderr)
        findings.append(f"aborted: {error}")
    finally:
        try:
            print("[memeval] Tearing down evaluation stores...", file=sys.stderr, flush=True)
            await teardown(session.gateways)
            print("[memeval] Teardown complete", file=sys.stderr, flush=True)
        finally:
            try:
                if pool is not None:
                    await pool.close()
            finally:
                scratch_removed = _remove_scratch_sqlite(environment.sqlite_path)

    _attach_stream_errors(session, recorded)
    seed_failure_ids = tuple(sorted({*findings, *session.seed_failures}))
    return MemoryShardResult(
        rows=rows,
        seed_failure_ids=seed_failure_ids,
        private_transcript=tuple(recorded),
        nonce=identity.nonce,
        provider_findings=tuple(sorted(set(findings))),
        scratch_removed=scratch_removed,
    )


def build_memory_report(
    probe_set: ProbeSet,
    shard_results: Sequence[MemoryShardResult],
    *,
    provider: str,
    model: str,
    ran_at: datetime,
) -> dict[str, object]:
    """Merge shard rows, then assemble the existing report exactly once."""

    rows = tuple(row for shard in shard_results for row in shard.rows)
    seed_failures = tuple(
        sorted({failure for shard in shard_results for failure in shard.seed_failure_ids})
    )
    nonces = {shard.nonce for shard in shard_results}
    nonce = next(iter(nonces)) if len(nonces) == 1 else ""
    return build_report(
        probe_set,
        rows,
        provider=provider,
        model=model,
        run_key=run_key(probe_set.probe_set_id, model, probe_set.seed),
        ran_at=ran_at,
        seed_failures=seed_failures,
        nonce=nonce,
    )
