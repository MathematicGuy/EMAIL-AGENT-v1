#!/usr/bin/env python3
"""Memory evaluation harness CLI. See tasks/specs/SPEC-memory-evaluation.md.

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
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cowork_agent.config import (
    GeminiSettings,
    load_runtime_environment,
)
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.default_project import (
    NullDefaultProjectEpisodes,
)
from cowork_agent.features.ai_chat.memory_eval.live_controller import AdapterSet
from cowork_agent.features.ai_chat.memory_eval.live_env import (
    LiveEnvironment,
    UnsafeTargetError,
    probe_environment,
    run_with_selector_loop,
    unavailable_scopes,
)
from cowork_agent.features.ai_chat.memory_eval.live_runner import (
    ExcessiveSeedFailuresError,
    LiveSession,
    ask_live,
    build_identity,
    teardown,
)
from cowork_agent.features.ai_chat.memory_eval.live_seeding import seed_semantic
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, load_probe_set
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_set
from cowork_agent.features.ai_chat.memory_eval.scoring import score

_DEFAULT_PROBE_SET = Path("evaluations/MEMORIES/probes/v2-four-scopes-wide.json")
_DEFAULT_OUTPUT_DIR = Path("evaluations/MEMORIES/baselines")
_DETAIL_DIR = Path("evaluations/MEMORIES/runs")

#: Chat providers this harness can drive, mirroring `evaluate_email_golden.py`.
_SUPPORTED_PROVIDERS = ("gemini", "openrouter", "groq", "mistral")


def _default_provider(environ: Mapping[str, str]) -> str:
    """Which provider to drive when `--provider` is not given.

    Follows `LLM_PROVIDER`, the same switch the rest of the project reads, so a
    checkout configured for one provider does not silently evaluate on another.
    """

    return environ.get("LLM_PROVIDER", "").strip().lower() or "gemini"


def _build_chat_reply(
    provider: str, environ: Mapping[str, str], model: str | None = None
) -> tuple[Any, str, str]:
    """Build the chat reply adapter for `provider`, with the model it will use.

    Returns the model name the provider actually answers with rather than a
    literal, because the report is compared across runs: a run labelled with a
    model that did not produce it cannot be compared against anything.

    `environ` is passed explicitly rather than left to `from_env()`'s default.
    That default reloads the `.env` file, which would put back any key the
    caller had deliberately removed - and for this harness a restored key means
    a real billed run against a real model.
    """

    if provider == "gemini":
        from cowork_agent.integrations.llm.chat_reply import GeminiChatReply

        gemini = GeminiSettings.from_env(environ)
        if model:
            gemini = replace(gemini, model=model)
        return GeminiChatReply.from_settings(gemini), provider, gemini.model
    if provider == "openrouter":
        from cowork_agent.config import OpenRouterSettings
        from cowork_agent.integrations.llm.chat_reply import OpenRouterChatReply

        openrouter = OpenRouterSettings.from_env(environ)
        if model:
            openrouter = replace(openrouter, model=model, allowed_models=(model,))
        return OpenRouterChatReply.from_settings(openrouter), provider, openrouter.model
    if provider == "groq":
        from cowork_agent.config import GroqSettings
        from cowork_agent.integrations.llm.chat_reply import GroqChatReply

        groq = GroqSettings.from_env(environ)
        if model:
            groq = replace(groq, model=model)
        return GroqChatReply.from_settings(groq), provider, groq.model
    if provider == "mistral":
        from cowork_agent.config import MistralSettings
        from cowork_agent.integrations.llm.chat_reply import MistralChatReply

        mistral = MistralSettings.from_env(environ)
        if model:
            mistral = replace(mistral, model=model)
        return MistralChatReply.from_settings(mistral), provider, mistral.model
    raise ValueError(f"unsupported provider for memory evaluation: {provider!r}")


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
    from cowork_agent.features.ai_chat.memory_eval.probes import ProbeSet

    if isinstance(probe_set, ProbeSet):
        print(
            f"[memeval-dry] Starting dry run: {probe_set.probe_set_id} "
            f"({len(probe_set.probes)} probes, {len(probe_set.probes) * 3} calls)",
            file=sys.stderr,
            flush=True,
        )

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
        # The harness builds its scopes directly, so they carry the legacy
        # "default-project" sentinel that the app resolves to a real UUID before
        # any episode is written. `task_episodes.project_id` is `uuid`, so the
        # sentinel would fail every write here. See `default_project`.
        episodic = NullDefaultProjectEpisodes(PostgresTaskEpisodeRepository(pool))
        print("[memeval] Initialized PostgreSQL repositories", file=sys.stderr, flush=True)
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
        print(
            f"[memeval] Initialized SQLite repository at {env.sqlite_path}",
            file=sys.stderr,
            flush=True,
        )

    semantic: object | None = None
    if env.embeddings_ready:
        # Use the app's own factory rather than naming a provider here. It
        # honours DOCUMENT_EMBEDDING_PROVIDER, so the corpus is embedded by
        # whatever the product embeds documents with. Hardcoding one provider
        # would measure a retrieval path the product no longer uses, which is
        # the "same system as shipped" rule in SPEC 12.1.
        from cowork_agent.integrations.rag.bootstrap import build_document_embedder

        print("[memeval] Seeding semantic memory corpus...", file=sys.stderr, flush=True)
        embedder, _dimensions = build_document_embedder()
        outcome, adapter = await seed_semantic(
            probe_set.seed,
            embedder,
            corpus_root=Path("."),
        )
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


async def run_live(
    probe_set: ProbeSet,
    env: LiveEnvironment,
    reply: Any,
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

    total_calls = len(probe_set.probes) * 3
    print(
        f"[memeval] Starting evaluation run: probe_set={probe_set.probe_set_id} "
        f"({len(probe_set.probes)} probes, {total_calls} calls) | "
        f"provider={provider} | model={model}",
        file=sys.stderr,
        flush=True,
    )

    identity = build_identity(probe_set, model)
    adapters, failures, pool = await _build_adapters(env, probe_set)
    failures.extend(item.reason for item in unavailable_scopes(env))
    session = LiveSession(
        identity=identity,
        adapters=adapters,
        reply=reply,
        seed=probe_set.seed,
    )
    recorded = transcript if transcript is not None else []
    call_idx = 0

    async def ask(probe: Probe, arm: Arm, masked: Any) -> tuple[str, int]:
        nonlocal call_idx
        call_idx += 1
        current_idx = call_idx
        target_name = probe.targets.value
        arm_name = arm.value
        print(
            f"[{current_idx:02d}/{total_calls:02d}] Asking probe '{probe.probe_id}' "
            f"(target: {target_name}, arm: {arm_name})...",
            file=sys.stderr,
            flush=True,
        )
        text, latency_ms = await ask_live(session, probe, arm, masked)
        result = score(text, probe)
        certainty = "certain" if result.certain else "uncertain"
        print(
            f"[{current_idx:02d}/{total_calls:02d}] Done probe '{probe.probe_id}' "
            f"[{arm_name}] -> outcome: {result.outcome.value} ({certainty}) [{latency_ms}ms]",
            file=sys.stderr,
            flush=True,
        )
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
            nonce=identity.nonce,
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
        print("[memeval] Tearing down evaluation stores...", file=sys.stderr, flush=True)
        await teardown(session.gateways)
        if pool is not None:
            await pool.close()
        print("[memeval] Teardown complete", file=sys.stderr, flush=True)
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
    parser.add_argument(
        "--provider",
        choices=_SUPPORTED_PROVIDERS,
        help="Chat provider to evaluate against; defaults to LLM_PROVIDER, else gemini.",
    )
    parser.add_argument(
        "--model",
        help="Model name override for the provider.",
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
        provider = args.provider or _default_provider(dict(os.environ))
        if provider == "gemini" and not env.gemini_ready:
            print(
                "ERROR: no GEMINI_API_KEY. Without a model there is no reply to "
                "score, so there is no run.",
                file=sys.stderr,
            )
            return 1
        try:
            reply, provider, model = _build_chat_reply(
                provider, dict(os.environ), model=args.model
            )
        except ValueError as error:
            # A provider is selected but unusable - a missing key, an unset
            # model, or a name this harness cannot drive. The same outcome as no
            # model at all, said differently so it is not mistaken for one.
            print(f"ERROR: {provider} is configured but unusable: {error}", file=sys.stderr)
            return 1
        try:
            report = run_with_selector_loop(
                run_live(
                    probe_set,
                    env,
                    reply,
                    provider=provider,
                    model=model,
                    transcript=transcript,
                )
            )
        except ExcessiveSeedFailuresError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1

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
                    # Without this, a detail file cannot be matched to the
                    # report it belongs to when two runs overlap.
                    "nonce": report.get("nonce"),
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
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # Loaded here rather than inside main(): without it the harness reads an
    # empty environment and reports "no GEMINI_API_KEY" on a machine with six
    # configured. Keeping it out of main() leaves the process environment
    # authoritative for callers — a test that clears GEMINI_API_KEY* to assert
    # the no-model exit path must not have them handed back by the .env in the
    # checkout, which would turn that unit test into a real billed run.
    load_runtime_environment()
    raise SystemExit(main())

