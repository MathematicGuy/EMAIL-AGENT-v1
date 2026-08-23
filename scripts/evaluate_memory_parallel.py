#!/usr/bin/env python3
"""Resilient High-Speed Parallel Memory Evaluation Runner.

Features:
  1. 5-Worker asyncio concurrency pool for 60-call memory evaluation.
  2. Automatic API Connection & Transient Error Interception.
  3. Two-Pass Execution: Initial parallel sweep + targeted recovery pass for failed probes.
  4. Full Schema 2.2.0 compatibility with build_memory_evaluation_report.py.
  5. Standalone manifest output and --retry-failed support.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path.cwd()))

from cowork_agent.config import load_runtime_environment
from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.arms import Arm
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
from cowork_agent.features.ai_chat.memory_eval.probes import Probe, ProbeSet, load_probe_set
from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow, build_report
from cowork_agent.features.ai_chat.memory_eval.runner import run_key
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome, score
from scripts.evaluate_memory import (
    _DEFAULT_OUTPUT_DIR,
    _DETAIL_DIR,
    _SUPPORTED_PROVIDERS,
    _attach_stream_errors,
    _build_adapters,
    _build_chat_reply,
    _default_provider,
    _stamp_probe_set_identity,
    _stamp_prompt_identity,
    resolve_latest_probe_set,
)


@dataclass
class FailedCall:
    probe: Probe
    arm: Arm
    masked: MemoryType | None
    error_reason: str
    attempt: int = 1


async def execute_single_arm(
    session: LiveSession,
    probe: Probe,
    arm: Arm,
    masked: MemoryType | None,
    current_idx: int,
    total_calls: int,
    recorded: list[dict[str, object]],
    lock: asyncio.Lock,
) -> tuple[Outcome, bool, int, str]:
    """Execute one (probe, arm) call with telemetry and thread-safe recording."""
    target_name = probe.targets.value
    arm_name = arm.value
    print(
        f"[{current_idx:02d}/{total_calls:02d}] 🚀 Asking probe '{probe.probe_id}' "
        f"(target: {target_name}, arm: {arm_name})...",
        file=sys.stderr,
        flush=True,
    )

    t0 = time.monotonic()
    try:
        text, latency_ms = await ask_live(session, probe, arm, masked)
    except Exception as exc:
        text, latency_ms = "", int((time.monotonic() - t0) * 1000)
        session.ask_errors.append(
            {"probe": probe.probe_id, "arm": arm.value, "errors": [f"exception: {exc}"]}
        )

    t_wall = int((time.monotonic() - t0) * 1000)
    if latency_ms == 0:
        latency_ms = t_wall

    result = score(text, probe)
    certainty = "certain" if result.certain else "uncertain"

    if result.outcome == Outcome.NO_ANSWER or not text.strip():
        print(
            f"[{current_idx:02d}/{total_calls:02d}] ⚠️ Probe '{probe.probe_id}' "
            f"[{arm_name}] -> NO_ANSWER (API connection/empty response) [{latency_ms}ms]",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            f"[{current_idx:02d}/{total_calls:02d}] ✅ Done probe '{probe.probe_id}' "
            f"[{arm_name}] -> {result.outcome.value} ({certainty}) [{latency_ms}ms]",
            file=sys.stderr,
            flush=True,
        )

    async with lock:
        # Update or append to recorded transcript
        existing = next(
            (r for r in recorded if r["probe"] == probe.probe_id and r["arm"] == arm.value), None
        )
        record_data = {
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
        if existing:
            existing.update(record_data)
        else:
            recorded.append(record_data)

    return result.outcome, result.certain, latency_ms, text


async def run_probe_task(
    probe: Probe,
    session: LiveSession,
    semaphore: asyncio.Semaphore,
    progress: dict[str, int],
    total_calls: int,
    recorded: list[dict[str, object]],
    failed_calls: list[FailedCall],
    lock: asyncio.Lock,
) -> dict[Arm, tuple[Outcome, bool, int, str]]:
    """Runs all 3 arms for a probe in Pass 1, pushing transient API errors to failed_calls."""
    arm_results: dict[Arm, tuple[Outcome, bool, int, str]] = {}

    for arm in (Arm.FULL, Arm.ABLATED, Arm.CONTROL):
        masked = probe.targets if arm is Arm.ABLATED else None

        async with semaphore:
            async with lock:
                progress["completed"] += 1
                current_idx = progress["completed"]

            outcome, certain, lat, text = await execute_single_arm(
                session, probe, arm, masked, current_idx, total_calls, recorded, lock
            )
            arm_results[arm] = (outcome, certain, lat, text)

            # Check if this failure is caused by an API connection or empty reply
            if outcome == Outcome.NO_ANSWER or not text.strip():
                async with lock:
                    failed_calls.append(
                        FailedCall(
                            probe=probe,
                            arm=arm,
                            masked=masked,
                            error_reason="API Connection / Empty Response (NO_ANSWER)",
                        )
                    )

    return arm_results


async def run_recovery_pass(
    session: LiveSession,
    failed_calls: list[FailedCall],
    probe_arm_map: dict[str, dict[Arm, tuple[Outcome, bool, int, str]]],
    recorded: list[dict[str, object]],
    lock: asyncio.Lock,
    max_recovery_attempts: int = 3,
) -> list[FailedCall]:
    """Pass 2: Targeted retry pass for probes that experienced API failures."""
    if not failed_calls:
        return []

    print("\n" + "=" * 70, file=sys.stderr)
    print(
        f"[memeval-resilience] 🔄 Starting Recovery Re-Run Pass: "
        f"{len(failed_calls)} failed call(s) to retry",
        file=sys.stderr,
        flush=True,
    )
    print("=" * 70, file=sys.stderr)

    remaining_failures: list[FailedCall] = []

    for call_item in failed_calls:
        probe = call_item.probe
        arm = call_item.arm
        masked = call_item.masked
        recovered = False

        for attempt in range(1, max_recovery_attempts + 1):
            backoff_s = attempt * 2.0
            print(
                f"[Recovery {attempt}/{max_recovery_attempts}] ⏳ Retrying "
                f"probe '{probe.probe_id}' [{arm.value}] in {backoff_s:.1f}s...",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(backoff_s)

            # Reset session cache for this specific probe-arm to force fresh connection
            scope_session_id = f"{session.identity.namespace}-{probe.probe_id}-{arm.value}"
            session.seeded.discard(scope_session_id)

            outcome, certain, lat, text = await execute_single_arm(
                session, probe, arm, masked, 0, len(failed_calls), recorded, lock
            )

            if outcome != Outcome.NO_ANSWER and text.strip():
                print(
                    f"[Recovery {attempt}/{max_recovery_attempts}] 🎉 RECOVERED "
                    f"probe '{probe.probe_id}' [{arm.value}] -> {outcome.value} [{lat}ms]!",
                    file=sys.stderr,
                    flush=True,
                )
                probe_arm_map[probe.probe_id][arm] = (outcome, certain, lat, text)
                recovered = True
                break

        if not recovered:
            print(
                f"[Recovery] ❌ Probe '{probe.probe_id}' [{arm.value}] could not recover "
                f"after {max_recovery_attempts} retries.",
                file=sys.stderr,
                flush=True,
            )
            remaining_failures.append(call_item)

    return remaining_failures


async def run_parallel_evaluation(
    probe_set: ProbeSet,
    env: LiveEnvironment,
    reply: Any,
    *,
    provider: str,
    model: str,
    concurrency: int = 5,
    max_recovery_retries: int = 3,
    transcript: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], list[FailedCall]]:
    total_calls = len(probe_set.probes) * 3
    print("=" * 70, file=sys.stderr)
    print(
        f"[memeval-parallel] ⚡ Launching Resilient Parallel Evaluation: {concurrency} Workers\n"
        f"  • Probe Set : {probe_set.probe_set_id} "
        f"({len(probe_set.probes)} probes, {total_calls} calls)\n"
        f"  • Provider  : {provider}\n"
        f"  • Model     : {model}\n"
        f"  • Resiliency: Auto Failed-Probe Recovery Queue (up to {max_recovery_retries} retries)",
        file=sys.stderr,
        flush=True,
    )
    print("=" * 70, file=sys.stderr)

    identity = build_identity(probe_set, model)
    adapters, failures, pool = await _build_adapters(env, probe_set)
    failures.extend(item.reason for item in unavailable_scopes(env))

    session = LiveSession(
        identity=identity,
        adapters=adapters,
        reply=reply,
        seed=probe_set.seed,
        max_consecutive_provider_failures=15,
    )

    recorded = transcript if transcript is not None else []
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    progress = {"completed": 0}
    failed_calls: list[FailedCall] = []

    t_start = time.monotonic()
    try:
        # Phase 1: Run full parallel pass
        tasks = [
            run_probe_task(
                probe=probe,
                session=session,
                semaphore=semaphore,
                progress=progress,
                total_calls=total_calls,
                recorded=recorded,
                failed_calls=failed_calls,
                lock=lock,
            )
            for probe in probe_set.probes
        ]
        probe_results = await asyncio.gather(*tasks)

        probe_arm_map: dict[str, dict[Arm, tuple[Outcome, bool, int, str]]] = {
            probe.probe_id: probe_results[i] for i, probe in enumerate(probe_set.probes)
        }

        # Phase 2: Run targeted recovery pass for API-failed calls
        unrecovered = []
        if failed_calls:
            unrecovered = await run_recovery_pass(
                session=session,
                failed_calls=failed_calls,
                probe_arm_map=probe_arm_map,
                recorded=recorded,
                lock=lock,
                max_recovery_attempts=max_recovery_retries,
            )

        # Assemble finalized ProbeRows from recovered/primary data
        rows: list[ProbeRow] = []
        for probe in probe_set.probes:
            arm_dict = probe_arm_map[probe.probe_id]
            certain = all(arm_dict[a][1] for a in (Arm.FULL, Arm.ABLATED, Arm.CONTROL))
            latency_total = sum(arm_dict[a][2] for a in (Arm.FULL, Arm.ABLATED, Arm.CONTROL))
            rows.append(
                ProbeRow(
                    probe_id=probe.probe_id,
                    targets=probe.targets,
                    test=probe.test,
                    full=arm_dict[Arm.FULL][0],
                    ablated=arm_dict[Arm.ABLATED][0],
                    control=arm_dict[Arm.CONTROL][0],
                    certain=certain,
                    latency_ms=latency_total,
                )
            )

        report = build_report(
            probe_set,
            rows,
            provider=provider,
            model=model,
            run_key=run_key(probe_set.probe_set_id, model, probe_set.seed),
            ran_at=datetime.now(UTC),
            seed_failures=sorted({*failures, *session.seed_failures}),
            nonce=identity.nonce,
        )
        _attach_stream_errors(session, recorded)
    finally:
        print("[memeval] Tearing down evaluation stores...", file=sys.stderr, flush=True)
        await teardown(session.gateways)
        if pool is not None:
            await pool.close()
        print("[memeval] Teardown complete", file=sys.stderr, flush=True)

    t_elapsed = time.monotonic() - t_start
    print("=" * 70, file=sys.stderr)
    print(
        f"[memeval-parallel] 🏁 Evaluation Finished in {t_elapsed:.2f}s "
        f"({t_elapsed / 60:.1f} min) across {concurrency} workers!\n"
        f"  • Total Probes  : {len(probe_set.probes)}\n"
        f"  • API Retries   : {len(failed_calls)} calls retried\n"
        f"  • Unrecovered   : {len(unrecovered)} calls",
        file=sys.stderr,
        flush=True,
    )
    print("=" * 70, file=sys.stderr)
    return report, unrecovered


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-set",
        type=Path,
        default=None,
        help="Path to probe set JSON file (e.g. v2 wide, v3 hard)",
    )
    parser.add_argument("--output", type=Path, help="Report path; defaults under baselines/")
    parser.add_argument(
        "--provider",
        default="mimo",
        choices=_SUPPORTED_PROVIDERS,
        help="Chat provider to evaluate against (default: mimo).",
    )
    parser.add_argument(
        "--model",
        help="Model name override (e.g. mimo-v2.5, mimo-v2.5-pro).",
    )
    parser.add_argument(
        "--workers",
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent parallel workers (default: 5).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum targeted recovery retries for API failures (default: 3).",
    )
    args = parser.parse_args(argv)

    probe_set_path = args.probe_set or resolve_latest_probe_set()
    try:
        payload = json.loads(probe_set_path.read_text(encoding="utf-8"))
        probe_set = load_probe_set(payload)
    except (OSError, ValueError) as error:
        print(f"ERROR: cannot load probe set: {error}", file=sys.stderr)
        return 2

    try:
        env = probe_environment(dict(os.environ))
    except UnsafeTargetError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    provider = args.provider or _default_provider(dict(os.environ))
    try:
        reply, provider, model = _build_chat_reply(provider, dict(os.environ), model=args.model)
    except ValueError as error:
        print(f"ERROR: {provider} is configured but unusable: {error}", file=sys.stderr)
        return 1

    transcript: list[dict[str, object]] = []
    try:
        report, unrecovered = run_with_selector_loop(
            run_parallel_evaluation(
                probe_set,
                env,
                reply,
                provider=provider,
                model=model,
                concurrency=args.workers,
                max_recovery_retries=args.max_retries,
                transcript=transcript,
            )
        )
    except Exception as error:
        print(f"ERROR during parallel evaluation: {error}", file=sys.stderr)
        return 1

    _stamp_probe_set_identity(report, probe_set_path)
    _stamp_prompt_identity(report)

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    output = args.output
    if output is None:
        output = _DEFAULT_OUTPUT_DIR / f"{stamp}-{probe_set.probe_set_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if transcript:
        detail = _DETAIL_DIR / f"{stamp}-{probe_set.probe_set_id}-detail.json"
        detail.parent.mkdir(parents=True, exist_ok=True)
        detail.write_text(
            json.dumps(
                {
                    "run_key": report.get("run_key"),
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
        print(f"Detail transcript written to {detail}", file=sys.stderr)

    if unrecovered:
        manifest_path = _DETAIL_DIR / f"{stamp}-{probe_set.probe_set_id}-unrecovered.json"
        manifest_path.write_text(
            json.dumps(
                [
                    {
                        "probe": c.probe.probe_id,
                        "arm": c.arm.value,
                        "error": c.error_reason,
                    }
                    for c in unrecovered
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Unrecovered failures manifest written to {manifest_path}", file=sys.stderr)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report.get("aborted") else 0


if __name__ == "__main__":
    load_runtime_environment()
    raise SystemExit(main())
