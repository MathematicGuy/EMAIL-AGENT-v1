#!/usr/bin/env python3
"""Memory evaluation harness CLI. See evaluations/MEMORIES/SPEC.md.

Exit codes:
  0 - the run completed and a report was written
  1 - the run could not produce a scorable result (no usable model)
  2 - the probe set could not be loaded

Exit code 0 does NOT mean the memory system is good. It means the harness ran.
Verdicts are read by a human; this harness reports, it does not gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cowork_agent.config import (
    GeminiSettings,
    JinaEmbeddingSettings,
    load_runtime_environment,
)
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_env import (
    LiveEnvironment,
    UnsafeTargetError,
    probe_environment,
    run_with_selector_loop,
    unavailable_scopes,
)
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    LiveSession,
    ask_live,
    build_identity,
    teardown,
)
from cowork_agent.features.ai_chat.memory_eval.live_seeding import seed_semantic
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, load_probe_set
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_set
from cowork_agent.features.ai_chat.memory_eval.scoring import score
from cowork_agent.integrations.llm.chat_reply import GeminiChatReply
from cowork_agent.integrations.rag.embeddings import JinaEmbeddingAdapter

_DEFAULT_PROBE_SET = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")
_DEFAULT_OUTPUT_DIR = Path("evaluations/MEMORIES/baselines")
_DETAIL_DIR = Path("evaluations/MEMORIES/runs")


def _scripted_ask(probe: Probe, arm: Arm, masked: object) -> tuple[str, int]:
    """A deterministic stand-in reply, for --dry-run only.

    It answers correctly under FULL and declines otherwise, which exercises the
    scoring, verdict and report paths without a model. It measures NOTHING
    about the real system and must never be used to make a decision.
    """

    del masked
    if arm is Arm.FULL:
        if probe.expect_refusal:
            return ("I don't have that information.", 0)
        return (" ".join(probe.expect_any), 0)
    return ("I don't have that information.", 0)


async def _dry_run(probe_set: object) -> dict[str, object]:
    async def ask(probe: Probe, arm: Arm, masked: object) -> tuple[str, int]:
        return _scripted_ask(probe, arm, masked)

    return await run_probe_set(
        probe_set,  # type: ignore[arg-type]
        ask,
        provider="dry-run",
        model="scripted",
        ran_at=datetime.now(UTC),
    )


async def _build_adapters(
    env: LiveEnvironment, probe_set: ProbeSet
) -> tuple[AdapterSet, list[str], Any]:
    """Build every adapter the environment can support. Absences are findings.

    Nothing here raises on a missing dependency. A scope whose adapter could not
    be built fails closed at the gateway, which is what an unavailable scope
    should look like, and the reason travels into the report instead of ending
    the run for the scopes that were fine. `unavailable_scopes` names the
    missing infrastructure; this function only reports what it tried and could
    not finish, so the two never say the same thing twice.
    """

    failures: list[str] = []
    declarative: object | None = None
    episodic: object | None = None
    pool: object | None = None
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
        episodic = PostgresTaskEpisodeRepository(pool)
    elif env.sqlite_path is not None:
        # The product backs long_term and episodic with SQLite whenever
        # database_url() is empty, and one SQLiteChatRepository serves both
        # roles — exactly as app.py wires them. Building only the Postgres pair
        # here would report two working scopes as unavailable.
        from cowork_agent.persistence.repositories.sqlite_chat import SQLiteChatRepository

        env.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite_chat = SQLiteChatRepository(env.sqlite_path)
        await sqlite_chat.initialize()
        declarative = sqlite_chat
        episodic = sqlite_chat

    semantic: object | None = None
    if env.jina_ready:
        outcome, adapter = await seed_semantic(
            probe_set.seed,
            JinaEmbeddingAdapter(JinaEmbeddingSettings.from_env()),
            corpus_root=Path("."),
        )
        if outcome.ok:
            semantic = adapter
        else:
            failures.append(f"semantic: {outcome.reason}")

    return AdapterSet(declarative, episodic, semantic), failures, pool


async def run_live(
    probe_set: ProbeSet,
    env: LiveEnvironment,
    gemini: GeminiSettings,
    *,
    provider: str,
    model: str,
    transcript: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Seed, probe under three arms, report, then delete everything created.

    `transcript` collects the full question and reply for every arm. The
    committed report is metadata-only by design, so without this the replies a
    human has to read to resolve an uncertain refusal do not survive the run.
    """

    identity = build_identity(probe_set, model)
    adapters, failures, pool = await _build_adapters(env, probe_set)
    failures.extend(item.reason for item in unavailable_scopes(env))
    session = LiveSession(
        identity=identity,
        adapters=adapters,
        reply=GeminiChatReply.from_settings(gemini),
        seed=probe_set.seed,
    )
    recorded = transcript if transcript is not None else []

    async def ask(probe: Probe, arm: Arm, masked: Any) -> tuple[str, int]:
        text, latency_ms = await ask_live(session, probe, arm, masked)
        result = score(text, probe)
        recorded.append(
            {
                "probe": probe.probe_id,
                "targets": probe.targets.value,
                "arm": arm.value,
                "masked": None if masked is None else str(masked.value),
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
        report = await run_probe_set(
            probe_set,
            ask,
            provider=provider,
            model=model,
            ran_at=datetime.now(UTC),
            seed_failures=failures,
        )
        # Seeding happens inside ask_live, so session.seed_failures is only
        # complete once run_probe_set has returned. Passing it as an argument
        # above would capture an empty list on every run, because arguments are
        # evaluated before the call.
        report["seed_failures"] = sorted({*failures, *session.seed_failures})
        for record in recorded:
            record["stream_errors"] = [
                item
                for item in session.ask_errors
                if item["probe"] == record["probe"] and item["arm"] == record["arm"]
            ]
    finally:
        # Teardown runs even when a probe raised. A run that created stores must
        # not leave them behind, and a partial cleanup still beats none.
        await teardown(session.gateways)
        if pool is not None:
            await pool.close()
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-set", type=Path, default=_DEFAULT_PROBE_SET)
    parser.add_argument("--output", type=Path, help="Report path; defaults under baselines/")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scripted replies. Validates harness mechanics only - never a result.",
    )
    args = parser.parse_args(argv)
    transcript: list[dict[str, object]] = []

    try:
        payload = json.loads(args.probe_set.read_text(encoding="utf-8"))
        probe_set = load_probe_set(payload)
    except (OSError, ValueError) as error:
        # ProbeSetError subclasses ValueError, so this catches both a missing
        # file and an unloadable probe set. Listing it separately would be a
        # redundant handler (ruff B014).
        print(f"ERROR: cannot load probe set: {error}", file=sys.stderr)
        return 2

    if args.dry_run:
        report = asyncio.run(_dry_run(probe_set))
    else:
        # A missing Postgres or Jina key is a per-scope finding the report
        # carries. A missing model is not: with no reply there is nothing to
        # score, so there is no run at all.
        try:
            env = probe_environment(dict(os.environ))
        except UnsafeTargetError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        if not env.gemini_ready:
            print(
                "ERROR: no GEMINI_API_KEY. Without a model there is no reply to "
                "score, so there is no run.",
                file=sys.stderr,
            )
            return 1
        try:
            gemini = GeminiSettings.from_env()
        except ValueError as error:
            # A key is present but unusable - the same outcome as no key, said
            # differently so it is not mistaken for an empty environment.
            print(f"ERROR: Gemini is configured but unusable: {error}", file=sys.stderr)
            return 1
        report = run_with_selector_loop(
            run_live(
                probe_set,
                env,
                gemini,
                provider="gemini",
                model=gemini.model,
                transcript=transcript,
            )
        )

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    output = args.output
    if output is None:
        output = _DEFAULT_OUTPUT_DIR / f"{stamp}-{probe_set.probe_set_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if transcript:
        # The committed report is metadata-only, so the replies live here
        # instead. `runs/` is gitignored: a row scored on the refusal phrase
        # list cannot be resolved without reading the text it was scored on.
        detail = _DETAIL_DIR / f"{stamp}-{probe_set.probe_set_id}-detail.json"
        detail.parent.mkdir(parents=True, exist_ok=True)
        detail.write_text(
            json.dumps(
                {
                    "run_key": report.get("run_key"),
                    "model": report.get("model"),
                    "ran_at": report.get("ran_at"),
                    "seed_failures": report.get("seed_failures"),
                    "arms": transcript,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"detail written to {detail}", file=sys.stderr)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    # Loaded here rather than inside main(): without it the harness reads an
    # empty environment and reports "no GEMINI_API_KEY" on a machine with six
    # configured. Keeping it out of main() leaves the process environment
    # authoritative for callers — a test that clears GEMINI_API_KEY* to assert
    # the no-model exit path must not have them handed back by the .env in the
    # checkout, which would turn that unit test into a real billed run.
    load_runtime_environment()
    raise SystemExit(main())
