"""One end-to-end proof that the live tier runs (SPEC §7).

Marked `live`: needs PostgreSQL, a Gemini key and a Jina key. Deselected by
default, so the standard suite stays green without any of them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cowork_agent.features.ai_chat.memory_eval.live_env import (
    probe_environment,
    unavailable_scopes,
)
from cowork_agent.features.ai_chat.memory_eval.probes import load_probe_set

pytestmark = pytest.mark.live

_PROBE_SET = Path("evaluations/MEMORIES/probes/v1-four-scopes.json")


def test_the_environment_reports_every_scope_available() -> None:
    env = probe_environment(dict(os.environ))
    missing = unavailable_scopes(env)
    if missing:
        pytest.skip(f"live dependencies missing: {[item.reason for item in missing]}")
    assert env.postgres_url is not None


def test_the_shipped_probe_set_runs_live_and_produces_one_row_per_probe() -> None:
    env = probe_environment(dict(os.environ))
    if unavailable_scopes(env) or not env.gemini_ready:
        pytest.skip("live dependencies missing")

    from scripts.evaluate_memory import main

    output = Path("evaluations/MEMORIES/runs/live-smoke.json")
    assert main(["--probe-set", str(_PROBE_SET), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    probe_set = load_probe_set(json.loads(_PROBE_SET.read_text(encoding="utf-8")))
    assert len(report["verdicts"]) == len(probe_set.probes)
    assert report["provider"] == "gemini"
