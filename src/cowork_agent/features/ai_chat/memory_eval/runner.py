"""Orchestrating one probe set across three arms (SPEC §7).

Deliberately linear. The comments name what each step prevents, because every
one of them exists to stop the harness measuring something other than memory.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from cowork_agent.domain.chat_contracts import MemoryType

from .arms import Arm
from .probes import Probe, ProbeSet, SeedSpec
from .report import ProbeRow, build_report
from .scoring import Outcome, score

# A callable the runner uses to ask one probe under one arm and get the reply
# text plus how long it took. The live implementation drives
# ChatController.stream_message; --dry-run supplies a scripted one.
AskProbe = Callable[[Probe, Arm, MemoryType | None], Awaitable[tuple[str, int]]]


def run_key(probe_set_id: str, model: str, seed: SeedSpec) -> str:
    """A stable id for this exact (probe set, model, seed) combination.

    It names the throwaway tenant so a run can never collide with another run
    or touch a real user's memory, and it is a staleness guard by construction:
    change the seed and you address a different tenant, so a run can never
    quietly probe a store that was seeded for a different question.
    """

    material = json.dumps(
        {
            "probe_set_id": probe_set_id,
            "model": model,
            "short_term": list(seed.short_term),
            "long_term": dict(sorted(seed.long_term.items())),
            "episodic": [[entry.request, entry.approve] for entry in seed.episodic],
            "semantic": seed.semantic_corpus_dir,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


async def run_probe_set(
    probe_set: ProbeSet,
    ask: AskProbe,
    *,
    provider: str,
    model: str,
    ran_at: datetime,
    seed_failures: Sequence[str] = (),
    nonce: str = "",
) -> dict[str, object]:
    """Ask every probe under all three arms and assemble the report."""

    rows: list[ProbeRow] = []

    for probe in probe_set.probes:
        outcomes: dict[Arm, Outcome] = {}
        certain = True
        latency_total = 0

        for arm in (Arm.FULL, Arm.ABLATED, Arm.CONTROL):
            # FULL and CONTROL read every scope; only ABLATED masks one. CONTROL
            # differs by having no seed at all, not by disabling reads - see
            # SPEC §5.1, this is the distinction the whole leak signal rests on.
            masked = probe.targets if arm is Arm.ABLATED else None
            reply, latency_ms = await ask(probe, arm, masked)
            latency_total += latency_ms

            result = score(reply, probe)
            if not result.certain:
                # Only refusal verdicts are uncertain. One uncertain arm makes
                # the whole row uncertain: the verdict is derived from all three.
                certain = False
            outcomes[arm] = result.outcome

        rows.append(
            ProbeRow(
                probe_id=probe.probe_id,
                targets=probe.targets,
                test=probe.test,
                full=outcomes[Arm.FULL],
                ablated=outcomes[Arm.ABLATED],
                control=outcomes[Arm.CONTROL],
                certain=certain,
                latency_ms=latency_total,
            )
        )

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
