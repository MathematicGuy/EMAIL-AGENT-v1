#!/usr/bin/env python3
"""Pre-evaluation Latency Gate (Light Harness).

Enforces the hard constraint: average latency must be < 9.0s across 5 samples
before any candidate model is admitted to the full evaluation queue.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / "src"))

from cowork_agent.config import (
    GeminiSettings,
    OpenRouterSettings,
    load_runtime_environment,
)
from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryContextResponse
from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
from cowork_agent.features.ai_chat.memory_eval.live_env import run_with_selector_loop

MAX_ALLOWED_AVG_LATENCY_SECONDS = 9.0
SAMPLE_COUNT = 5

DEFAULT_PROBES = [
    "Tôi đang xử lý yêu cầu gia hạn CCCD cho văn phòng Đà Nẵng. Yêu cầu này là cho văn phòng nào?",
    "Hạn chót của yêu cầu gia hạn CCCD đã dời sang thứ Tư. Hạn chót mới là khi nào?",
    "Tôi đã đặt bạn ở vai trò nào khi trả lời tôi?",
    "Chính sách công ty yêu cầu nộp đề nghị làm thêm giờ qua biểu mẫu nào?",
    "Tạo một tác vụ gia hạn CCCD cho văn phòng Đà Nẵng.",
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
    if provider == "groq":
        from cowork_agent.config import GroqSettings
        from cowork_agent.integrations.llm.chat_reply import GroqChatReply

        groq_settings = GroqSettings.from_env(environ)
        return GroqChatReply.from_settings(replace(groq_settings, model=model))
    if provider == "mistral":
        from cowork_agent.config import MistralSettings
        from cowork_agent.integrations.llm.chat_reply import MistralChatReply

        mistral_settings = MistralSettings.from_env(environ)
        return MistralChatReply.from_settings(replace(mistral_settings, model=model))
    raise ValueError(f"Unsupported provider for latency gate: {provider!r}")


def run_latency_gate(provider: str, model: str) -> bool:
    print(f"=== PRE-EVALUATION ADMISSION GATE: Provider={provider} | Model={model} ===")
    print("Checklist Requirements:")
    print(
        f"  1. Latency Budget: Average Latency < {MAX_ALLOWED_AVG_LATENCY_SECONDS}s "
        f"across {SAMPLE_COUNT} samples"
    )
    print(
        "  2. Schema Adherence: Must correctly parse hard structured responses "
        "(task_proposal, titles, citations)\n"
    )

    reply = _build_reply(provider, model)
    latencies_ms: list[float] = []
    schema_failures: list[str] = []

    for i, probe_text in enumerate(DEFAULT_PROBES[:SAMPLE_COUNT], 1):
        req = ChatMessageRequest(
            session_id=f"latency-gate-{i}",
            user_message=probe_text,
            idempotency_key=f"latency-gate-{i}",
        )
        ctx = assemble_generation_context(
            req, MemoryContextResponse((), None, (), None, False, ())
        )

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

            # Check hard structured proposal if probe was explicit task creation
            is_task_request = "Tạo một tác vụ" in probe_text or "Lập kế hoạch" in probe_text
            schema_status = ""
            if is_task_request:
                if task_proposal is None:
                    schema_failures.append(
                        f"Sample {i}: Missing task_proposal on explicit task request"
                    )
                    schema_status = " [SCHEMA ERROR: task_proposal is None]"
                else:
                    task_title = getattr(task_proposal, "task_title", "")
                    action_plan = getattr(task_proposal, "action_plan", ())
                    if not task_title or not action_plan:
                        schema_failures.append(f"Sample {i}: Incomplete task_proposal fields")
                        schema_status = " [SCHEMA ERROR: incomplete fields]"
                    else:
                        clean_title = str(task_title).encode("ascii", "replace").decode("ascii")
                        schema_status = f" [HARD SCHEMA OK: '{clean_title}']"

            print(
                f"  [Sample {i}/{SAMPLE_COUNT}] -> SUCCESS: {dur_ms:.1f}ms ({dur_ms/1000:.2f}s)"
                f"{schema_status} | Reply: {clean_preview}..."
            )
        except Exception as exc:
            cause = getattr(exc, "__cause__", None) or exc
            schema_failures.append(f"Sample {i}: Generation exception {type(cause).__name__}")
            print(f"  [Sample {i}/{SAMPLE_COUNT}] -> FAILED: {type(cause).__name__}: {cause}")

    if not latencies_ms:
        print(f"\n[GATE RESULT: REJECTED] Model {model} failed all {SAMPLE_COUNT} samples.")
        return False

    avg_latency_s = (sum(latencies_ms) / len(latencies_ms)) / 1000.0
    latency_passed = (
        avg_latency_s < MAX_ALLOWED_AVG_LATENCY_SECONDS and len(latencies_ms) == SAMPLE_COUNT
    )
    schema_passed = len(schema_failures) == 0

    print("\n--- Admission Gate Checklist Summary ---")
    print(f"  [1] Turns Completed   : {len(latencies_ms)}/{SAMPLE_COUNT}")
    print(
        f"  [2] Average Latency   : {avg_latency_s:.2f}s "
        f"({'PASSED' if latency_passed else 'FAILED'} - "
        f"Threshold: < {MAX_ALLOWED_AVG_LATENCY_SECONDS}s)"
    )
    fail_detail = f" ({len(schema_failures)} errors: {schema_failures})" if schema_failures else ""
    print(
        f"  [3] Hard Schema Check : "
        f"{'PASSED' if schema_passed else f'FAILED{fail_detail}'}"
    )

    if latency_passed and schema_passed:
        print(f"\n[GATE RESULT: PASSED] Model {model} satisfies all admission criteria.")
        return True
    print(f"\n[GATE RESULT: REJECTED] Model {model} failed admission criteria.")
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default="gemini",
        choices=["gemini", "openrouter", "groq", "mistral"],
    )
    parser.add_argument("--model", required=True, help="Model identifier to probe")
    args = parser.parse_args(argv)

    passed = run_latency_gate(args.provider, args.model)
    return 0 if passed else 1


if __name__ == "__main__":
    load_runtime_environment()
    raise SystemExit(main())
