#!/usr/bin/env python3
"""Opt-in concurrent Mistral credential smoke gate.

This command sends one fixed synthetic chat request through every selected
credential alias. It reports only safe attempt metadata and never retries. A
zero exit code is an observed smoke signal for enabling more than one worker,
not a claim that configuration alone proves independent provider quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryContextResponse
from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
from cowork_agent.features.batch_evaluation.contracts import ProviderAttemptEvent
from cowork_agent.features.batch_evaluation.credentials import (
    CredentialLease,
    CredentialLeasingPool,
)
from cowork_agent.integrations.llm.evaluation_mistral import MistralEvaluationReplyFactory

_DEFAULT_OUTPUT = Path(".data/evaluation-key-smoke.json")
_DEFAULT_MODEL = "mistral-small-2603"
_SYNTHETIC_PROMPT = "Reply with the word ready. This is a synthetic key-independence smoke check."


@dataclass(frozen=True, slots=True)
class _Observation:
    alias: str
    status_class: str
    latency_ms: int
    completed_at_ms: int
    rate_limited_at_ms: int | None

    def public(self) -> dict[str, object]:
        return {
            "alias": self.alias,
            "status_class": self.status_class,
            "latency_ms": self.latency_ms,
        }


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer >= 1") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be an integer >= 1")
    return parsed


def _model_from(environ: Mapping[str, str]) -> str:
    model = environ.get("MISTRAL_MODEL", _DEFAULT_MODEL).strip()
    if not model or model.startswith("replace-with-"):
        raise ValueError("no usable Mistral model is configured")
    return model


def _request_and_context() -> tuple[ChatMessageRequest, object]:
    request = ChatMessageRequest(
        session_id="mistral-key-smoke",
        user_message=_SYNTHETIC_PROMPT,
        idempotency_key="mistral-key-smoke-request",
    )
    context = assemble_generation_context(
        request,
        MemoryContextResponse(
            turns=(),
            profile=None,
            episodes=(),
            semantic_context=None,
            degraded=False,
            degraded_sources=(),
        ),
    )
    return request, context


async def _smoke_alias(
    lease: CredentialLease,
    *,
    model: str,
    started_at: float,
    barrier: asyncio.Barrier,
    factory: MistralEvaluationReplyFactory,
) -> _Observation:
    events: list[ProviderAttemptEvent] = []
    request, context = _request_and_context()
    await barrier.wait()
    try:
        async with lease:
            reply = factory.bind(lease, model, events.append)
            try:
                async for _chunk in reply.stream_reply(request, context):
                    pass
            except Exception:
                # The safe attempt event is public; exception text can contain
                # transport details.
                pass
    except Exception:
        pass

    completed_at_ms = max(0, int((time.monotonic() - started_at) * 1000))
    if not events:
        return _Observation(lease.alias, "failed", 0, completed_at_ms, None)
    event = events[-1]
    rate_limited_at_ms = (
        completed_at_ms
        if event.status_code == 429
        else None
    )
    return _Observation(
        alias=event.credential_alias,
        status_class=event.outcome,
        latency_ms=event.latency_ms,
        completed_at_ms=completed_at_ms,
        rate_limited_at_ms=rate_limited_at_ms,
    )


def _cross_key_429_timing(observations: Sequence[_Observation]) -> list[dict[str, object]]:
    rate_limited = [item for item in observations if item.rate_limited_at_ms is not None]
    return [
        {
            "first_alias": first.alias,
            "second_alias": second.alias,
            "delta_ms": abs(first.rate_limited_at_ms - second.rate_limited_at_ms),
        }
        for index, first in enumerate(rate_limited)
        for second in rate_limited[index + 1 :]
    ]


def _independence_demonstrated(observations: Sequence[_Observation]) -> bool:
    if len(observations) < 2:
        return False
    status_classes = {item.status_class for item in observations}
    if status_classes == {"succeeded"}:
        return True
    if not status_classes <= {"succeeded", "rate_limited"}:
        return False
    successful = [item for item in observations if item.status_class == "succeeded"]
    rate_limited = [item for item in observations if item.rate_limited_at_ms is not None]
    return any(
        success.completed_at_ms >= rate_limit.rate_limited_at_ms
        for success in successful
        for rate_limit in rate_limited
    )


async def run_smoke(
    *,
    workers: int,
    environ: Mapping[str, str],
) -> dict[str, object]:
    pool = CredentialLeasingPool.from_env("MISTRAL_API_KEY", environ)
    effective_workers = min(workers, pool.healthy_count)
    report: dict[str, object] = {
        "requested_workers": workers,
        "effective_workers": effective_workers,
        "healthy_key_count": pool.healthy_count,
        "requests": [],
        "cross_key_429_timing": [],
        "independence_demonstrated": False,
    }
    if effective_workers == 0:
        return report

    model = _model_from(environ)
    leases = await asyncio.gather(*(pool.lease() for _ in range(effective_workers)))
    started_at = time.monotonic()
    barrier = asyncio.Barrier(effective_workers)
    observations = await asyncio.gather(
        *(
            _smoke_alias(
                lease,
                model=model,
                started_at=started_at,
                barrier=barrier,
                factory=MistralEvaluationReplyFactory(),
            )
            for lease in leases
        )
    )
    ordered = tuple(sorted(observations, key=lambda item: item.alias))
    report["requests"] = [item.public() for item in ordered]
    report["cross_key_429_timing"] = _cross_key_429_timing(ordered)
    report["independence_demonstrated"] = _independence_demonstrated(ordered)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=_positive_int, required=True)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_smoke(workers=args.workers, environ=dict(os.environ)))
    except ValueError:
        print("ERROR: Mistral smoke configuration is unusable.", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report["effective_workers"] < report["requested_workers"]:
        print(
            "WORKER_COUNT_REDUCED "
            f"requested_workers={report['requested_workers']} "
            f"effective_workers={report['effective_workers']}",
            file=sys.stderr,
        )
    if not report["independence_demonstrated"]:
        print("KEY_INDEPENDENCE_NOT_DEMONSTRATED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
