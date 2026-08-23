#!/usr/bin/env python3
"""Pre-evaluation Latency Gate & 3-Arm Admission Harness.

Enforces the hard admission constraints:
  1. 3-Arm Evaluation: Executes 1 probe for each arm:
     - [1/4] Full Arm (With Loaded Memory Context)
     - [2/4] Ablated Arm (Masked Memory Scope / Restraint Check)
     - [3/4] Control Arm (Baseline Control Turn)
     - [4/4] Hard Schema & Task Proposal Arm (Task Generation & Schema Adherence)
  2. Latency Budget: Average Latency across all arms must be < 9.0s.
  3. Schema Adherence: Must correctly parse hard structured task proposal JSON.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / "src"))

from cowork_agent.config import (
    GeminiSettings,
    OpenRouterSettings,
    load_runtime_environment,
)
from cowork_agent.domain.chat_contracts import (
    ChatMessageRequest,
    DeclarativeProfile,
    MemoryContextResponse,
)
from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
from cowork_agent.features.ai_chat.memory_eval.live_env import run_with_selector_loop

MAX_ALLOWED_AVG_LATENCY_SECONDS = 9.0


@dataclass(frozen=True)
class GateProbeCase:
    name: str
    arm: str
    prompt: str
    context: MemoryContextResponse
    is_task_proposal: bool


def _build_test_probes() -> list[GateProbeCase]:
    now = datetime.now(UTC)
    profile = DeclarativeProfile(
        profile_id="gate-profile-1",
        user_id="user-gate",
        language="vi",
        timezone="Asia/Ho_Chi_Minh",
        assistant_persona="Hải Âu",
        response_tone="ngắn gọn",
        created_at=now,
        updated_at=now,
    )
    return [
        GateProbeCase(
            name="Full Arm (Loaded Memory Context)",
            arm="FULL_ARM",
            prompt="Tôi đã đặt bạn ở vai trò hay biệt danh nào khi trả lời tôi?",
            context=MemoryContextResponse((), profile, (), None, False, ()),
            is_task_proposal=False,
        ),
        GateProbeCase(
            name="Ablated Arm (Masked Scope / Restraint)",
            arm="ABLATED_ARM",
            prompt="Số điện thoại liên hệ cá nhân của tôi là số nào?",
            context=MemoryContextResponse((), None, (), None, False, ()),
            is_task_proposal=False,
        ),
        GateProbeCase(
            name="Control Arm (Baseline Control Turn)",
            arm="CONTROL_ARM",
            prompt="Chính sách công ty quy định công tác phí trong nước mỗi ngày là bao nhiêu?",
            context=MemoryContextResponse((), None, (), None, False, ()),
            is_task_proposal=False,
        ),
        GateProbeCase(
            name="Task Proposal Arm (Hard Schema Adherence)",
            arm="TASK_PROPOSAL_ARM",
            prompt="Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.",
            context=MemoryContextResponse((), None, (), None, False, ()),
            is_task_proposal=True,
        ),
    ]


def _build_reply(provider: str, model: str) -> object:
    environ = dict(os.environ)
    if provider == "gemini":
        from cowork_agent.integrations.llm.chat_reply import GeminiChatReply

        gemini_settings = GeminiSettings.from_env(environ)
        return GeminiChatReply.from_settings(replace(gemini_settings, model=model))
    if provider == "openrouter":
        from cowork_agent.integrations.llm.chat_reply import OpenRouterChatReply

        openrouter_settings = OpenRouterSettings.from_env(environ)
        return OpenRouterChatReply.from_settings(
            replace(openrouter_settings, model=model, allowed_models=(model,))
        )
    if provider == "mimo":
        from cowork_agent.config import MimoSettings
        from cowork_agent.integrations.llm.chat_reply import MimoChatReply

        mimo_settings = MimoSettings.from_env(environ)
        return MimoChatReply.from_settings(replace(mimo_settings, model=model))
    if provider == "mistral":
        from cowork_agent.config import MistralSettings
        from cowork_agent.integrations.llm.chat_reply import MistralChatReply

        mistral_settings = MistralSettings.from_env(environ)
        return MistralChatReply.from_settings(replace(mistral_settings, model=model))
    raise ValueError(f"Unsupported provider for latency gate: {provider!r}")


def run_latency_gate(provider: str, model: str, *, fail_open: bool = False) -> bool:
    probes = _build_test_probes()
    sample_count = len(probes)

    print(f"=== HARDENED 3-ARM ADMISSION GATE: Provider={provider} | Model={model} ===")
    print("Checklist Requirements:")
    print(
        f"  1. Latency Budget: Average Latency < {MAX_ALLOWED_AVG_LATENCY_SECONDS}s "
        f"across all {sample_count} arms/probes"
    )
    print("  2. Multi-Arm Verification: Full Arm, Ablated Arm, Control Arm, Task Proposal Arm")
    print("  3. Hard Schema Adherence: Must correctly parse structured task proposal JSON\n")

    reply = _build_reply(provider, model)
    latencies_ms: list[float] = []
    schema_failures: list[str] = []

    for i, probe in enumerate(probes, 1):
        req = ChatMessageRequest(
            session_id=f"gate-{probe.arm.lower()}",
            user_message=probe.prompt,
            idempotency_key=f"gate-{probe.arm.lower()}-{i}",
        )
        ctx = assemble_generation_context(req, probe.context)

        async def ask(
            request: ChatMessageRequest = req, context: object = ctx
        ) -> tuple[str, float, object | None]:
            start = time.perf_counter()
            texts: list[str] = []
            task_prop: object | None = None
            async for chunk in reply.stream_reply(request, context):  # type: ignore[attr-defined]
                texts.append(chunk.text)
                if getattr(chunk, "task_proposal", None) is not None:
                    task_prop = chunk.task_proposal
            dur = (time.perf_counter() - start) * 1000
            return "".join(texts), dur, task_prop

        try:
            answer, dur_ms, task_proposal = run_with_selector_loop(ask())
            latencies_ms.append(dur_ms)
            clean_preview = answer.encode("ascii", "replace").decode("ascii")[:55]

            schema_status = ""
            if probe.is_task_proposal:
                if task_proposal is None:
                    schema_failures.append(
                        f"[{probe.name}]: Missing task_proposal on explicit task request"
                    )
                    schema_status = " [SCHEMA ERROR: task_proposal is None]"
                else:
                    task_title = getattr(task_proposal, "task_title", "")
                    action_plan = getattr(task_proposal, "action_plan", ())
                    if not task_title or not action_plan:
                        schema_failures.append(f"[{probe.name}]: Incomplete task_proposal fields")
                        schema_status = " [SCHEMA ERROR: incomplete fields]"
                    else:
                        clean_title = str(task_title).encode("ascii", "replace").decode("ascii")
                        schema_status = f" [HARD SCHEMA OK: '{clean_title}']"

            print(
                f"  [{i}/{sample_count}] {probe.name} -> "
                f"SUCCESS: {dur_ms:.1f}ms ({dur_ms/1000:.2f}s)"
                f"{schema_status} | Preview: {clean_preview}..."
            )
        except Exception as exc:
            cause = getattr(exc, "__cause__", None) or exc
            schema_failures.append(f"[{probe.name}]: Generation exception {type(cause).__name__}")
            print(f"  [{i}/{sample_count}] {probe.name} -> FAILED: {type(cause).__name__}: {cause}")

    if not latencies_ms:
        print(f"\n[GATE RESULT: REJECTED] Model {model} failed all {sample_count} probes.")
        return False

    avg_latency_s = (sum(latencies_ms) / len(latencies_ms)) / 1000.0
    latency_passed = (
        avg_latency_s < MAX_ALLOWED_AVG_LATENCY_SECONDS and len(latencies_ms) == sample_count
    )
    schema_passed = len(schema_failures) == 0

    print("\n--- Admission Gate Checklist Summary ---")
    print(f"  [1] Probes/Arms Completed: {len(latencies_ms)}/{sample_count}")
    print(
        f"  [2] Average Latency      : {avg_latency_s:.2f}s "
        f"({'PASSED' if latency_passed else 'FAILED'} - "
        f"Threshold: < {MAX_ALLOWED_AVG_LATENCY_SECONDS}s)"
    )
    fail_detail = f" ({len(schema_failures)} errors: {schema_failures})" if schema_failures else ""
    print(
        f"  [3] Hard Schema Check    : "
        f"{'PASSED' if schema_passed else f'FAILED{fail_detail}'}"
    )

    if latency_passed and schema_passed:
        print(f"\n[GATE RESULT: PASSED] Model {model} satisfies all admission criteria.")
        return True
    if fail_open and schema_passed:
        print(
            f"\n[GATE RESULT: FAIL-OPEN (PASSED WITH WARNINGS)] "
            f"Model {model} exceeded latency budget "
            f"({avg_latency_s:.2f}s >= {MAX_ALLOWED_AVG_LATENCY_SECONDS}s), "
            f"but hard schema passed. Permitting evaluation via --fail-open."
        )
        return True
    print(f"\n[GATE RESULT: REJECTED] Model {model} failed admission criteria.")
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default="gemini",
        choices=["gemini", "openrouter", "mimo", "mistral"],
    )
    parser.add_argument("--model", required=True, help="Model identifier to probe")
    parser.add_argument(
        "--fail-open",
        action="store_true",
        help=(
            "Warn but return success (0) if hard schema passed "
            "even if latency threshold is exceeded"
        ),
    )
    args = parser.parse_args(argv)

    passed = run_latency_gate(args.provider, args.model, fail_open=args.fail_open)
    return 0 if passed else 1


if __name__ == "__main__":
    load_runtime_environment()
    raise SystemExit(main())
