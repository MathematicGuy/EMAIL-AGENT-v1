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
import hashlib
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
from cowork_agent.features.ai_chat.memory_eval import live_execution
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
from cowork_agent.features.ai_chat.memory_eval.live_env import (
    LiveEnvironment,
    UnsafeTargetError,
    probe_environment,
    run_with_selector_loop,
)
from cowork_agent.features.ai_chat.memory_eval.live_execution import (
    build_memory_report,
    execute_memory_shard,
)
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, load_probe_set
from cowork_agent.features.ai_chat.memory_eval.runner import run_probe_set

_ENV_MAX_CONSECUTIVE_PROVIDER_FAILURES = "MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES"
_DEFAULT_MAX_CONSECUTIVE_PROVIDER_FAILURES = 3


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"must be an integer >= 1, got {value!r}"
        ) from error
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be an integer >= 1, got {value!r}")
    return parsed


def _resolve_max_consecutive_provider_failures(
    cli_value: int | None, environ: Mapping[str, str]
) -> int:
    """CLI > env > 3. Rejects non-integers and values below 1."""

    if cli_value is not None:
        if cli_value < 1:
            raise ValueError(
                "--max-consecutive-provider-failures must be an integer >= 1, "
                f"got {cli_value}"
            )
        return cli_value
    raw = environ.get(_ENV_MAX_CONSECUTIVE_PROVIDER_FAILURES, "").strip()
    if not raw:
        return _DEFAULT_MAX_CONSECUTIVE_PROVIDER_FAILURES
    try:
        parsed = int(raw)
    except ValueError:
        raise ValueError(
            f"{_ENV_MAX_CONSECUTIVE_PROVIDER_FAILURES} must be an integer >= 1, "
            f"got {raw!r}"
        ) from None
    if parsed < 1:
        raise ValueError(
            f"{_ENV_MAX_CONSECUTIVE_PROVIDER_FAILURES} must be an integer >= 1, "
            f"got {raw!r}"
        )
    return parsed

_DEFAULT_PROBES_DIR = Path("evaluations/MEMORIES/probes")
_DEFAULT_OUTPUT_DIR = Path("evaluations/MEMORIES/baselines")
_DETAIL_DIR = Path("evaluations/MEMORIES/runs")


def resolve_latest_probe_set(probes_dir: Path | None = None) -> Path:
    """Find the highest version probe set JSON file in evaluations/MEMORIES/probes."""
    base_dir = probes_dir or _DEFAULT_PROBES_DIR
    if not base_dir.exists():
        return base_dir / "v2-four-scopes-wide.json"
    files = [f for f in base_dir.glob("*.json") if f.is_file()]
    if not files:
        return base_dir / "v2-four-scopes-wide.json"

    def _version_key(path: Path) -> tuple[int, str]:
        name = path.stem.lower()
        if name.startswith("v"):
            prefix = name[1:].split("-")[0].split("_")[0]
            try:
                return (int(prefix), str(path))
            except ValueError:
                pass
        return (0, str(path))

    return max(files, key=_version_key)


def _stamp_probe_set_identity(report: dict[str, object], probe_set_path: Path) -> None:
    """Record which probe file produced this baseline (SPEC §7.2)."""

    resolved = probe_set_path.resolve()
    try:
        stamped_path = resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        stamped_path = resolved.as_posix()
    report["probe_set_path"] = stamped_path
    report["probe_set_sha256"] = hashlib.sha256(probe_set_path.read_bytes()).hexdigest()


#: Chat providers this harness can drive, mirroring `evaluate_email_golden.py`.
_SUPPORTED_PROVIDERS = ("gemini", "openrouter", "vyce", "vyne", "mistral")


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
    if provider in ("vyce", "vyne"):
        from cowork_agent.config import VyceSettings
        from cowork_agent.integrations.llm.chat_reply import VyceChatReply

        vyce = VyceSettings.from_env(environ)
        if model:
            vyce = replace(vyce, model=model)
        return VyceChatReply.from_settings(vyce), provider, vyce.model
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


async def run_live(
    probe_set: ProbeSet,
    env: LiveEnvironment,
    reply: Any,
    *,
    provider: str,
    model: str,
    transcript: list[dict[str, object]] | None = None,
    max_consecutive_provider_failures: int = 3,
) -> dict[str, object]:
    """Compatibility wrapper around one full-set live shard."""

    transcript_size = len(transcript) if transcript is not None else 0
    result = await execute_memory_shard(
        probe_set,
        env,
        reply,
        provider=provider,
        model=model,
        max_consecutive_provider_failures=max_consecutive_provider_failures,
        private_transcript_sink=transcript,
    )
    # Compatibility for callers/tests using an older shard adapter which
    # returns its evidence rather than writing to the caller-owned sink.
    if transcript is not None and len(transcript) == transcript_size:
        transcript.extend(result.private_transcript)
    report = build_memory_report(
        probe_set,
        (result,),
        provider=provider,
        model=model,
        ran_at=datetime.now(UTC),
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-set",
        type=Path,
        default=None,
        help="Path to probe set JSON definition; defaults to the latest version found in probes/.",
    )
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
    parser.add_argument(
        "--max-consecutive-provider-failures",
        type=_positive_int,
        default=None,
        help=(
            "Abort after this many consecutive chat_provider_unavailable seed or "
            "ask failures. Default 3; env "
            f"{_ENV_MAX_CONSECUTIVE_PROVIDER_FAILURES}."
        ),
    )
    args = parser.parse_args(argv)
    try:
        max_consecutive = _resolve_max_consecutive_provider_failures(
            args.max_consecutive_provider_failures, dict(os.environ)
        )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    transcript: list[dict[str, object]] = []

    probe_set_path = args.probe_set or resolve_latest_probe_set()
    try:
        payload = json.loads(probe_set_path.read_text(encoding="utf-8"))
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
                    max_consecutive_provider_failures=max_consecutive,
                )
            )
        except live_execution.ExcessiveSeedFailuresError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1

    _stamp_probe_set_identity(report, probe_set_path)

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
    return 1 if report.get("aborted") else 0


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
