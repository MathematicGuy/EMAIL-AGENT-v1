from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec
from cowork_agent.features.ai_chat.memory_eval.runner import run_key
from scripts.evaluate_memory import main
from tests.unit.scripts.cli_harness import run_cli


def _probe_set_file(tmp_path: Path) -> Path:
    payload = {
        "schema_version": "2.0.0",
        "probe_set_id": "unit",
        "label": "unit",
        "seed": {"short_term": ["a turn"], "long_term": {}, "episodic": [], "semantic": None},
        "probes": [
            {
                "id": "st_recall_01",
                "targets": "short_term",
                "test": "recall",
                "question": "what did I say?",
                "expect_any": ["a turn"],
            }
        ],
    }
    path = tmp_path / "probes.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_run_key_is_stable_for_the_same_inputs() -> None:
    seed = SeedSpec(("a",), {}, (), None)
    assert run_key("set", "model", seed) == run_key("set", "model", seed)


def test_run_key_changes_when_the_seed_changes() -> None:
    assert run_key("set", "model", SeedSpec(("a",), {}, (), None)) != run_key(
        "set", "model", SeedSpec(("b",), {}, (), None)
    )


def test_run_key_changes_when_the_model_changes() -> None:
    seed = SeedSpec(("a",), {}, (), None)
    assert run_key("set", "model-a", seed) != run_key("set", "model-b", seed)


def test_dry_run_writes_a_report_and_exits_zero(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    code = main(
        ["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "2.2.0"
    assert report["probe_set_id"] == "unit"
    assert len(report["verdicts"]) == 1


def test_dry_run_stamps_probe_set_path_and_sha256(tmp_path: Path) -> None:
    probe_path = _probe_set_file(tmp_path)
    output = tmp_path / "report.json"
    code = main(
        ["--dry-run", "--probe-set", str(probe_path), "--output", str(output)]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["probe_set_sha256"] == hashlib.sha256(probe_path.read_bytes()).hexdigest()
    stamped = str(report["probe_set_path"])
    assert "\\" not in stamped
    assert probe_path.name in stamped


def test_resolve_latest_probe_set_returns_v3_path(tmp_path: Path) -> None:
    from scripts.evaluate_memory import resolve_latest_probe_set

    v2 = tmp_path / "v2-four-scopes-wide.json"
    v3 = tmp_path / "v3-50-probes.json"
    v2.write_text("{}", encoding="utf-8")
    v3.write_text("{}", encoding="utf-8")
    assert resolve_latest_probe_set(tmp_path) == v3


def test_dry_run_report_contains_no_probe_text(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    main(["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)])
    assert "what did I say?" not in output.read_text(encoding="utf-8")


def test_an_invalid_probe_set_exits_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "9.9.9"}), encoding="utf-8")
    assert main(["--dry-run", "--probe-set", str(bad)]) == 2


def test_a_missing_probe_set_exits_two(tmp_path: Path) -> None:
    assert main(["--dry-run", "--probe-set", str(tmp_path / "nope.json")]) == 2


@pytest.mark.live
def test_live_run_requires_a_database_and_key() -> None:
    pytest.skip("live tier: run manually with DATABASE_URL and a provider key set")


def test_a_live_run_without_a_gemini_key_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No model means no reply to score, so there is no run at all. This is the
    # one dependency whose absence is fatal rather than a per-scope finding.
    # Every GEMINI_API_KEY* name has to go: a .env sitting in the checkout
    # supplies numbered keys, and leaving one behind would turn this unit test
    # into a real billed run against a real model.
    for name in [item for item in os.environ if item.startswith("GEMINI_API_KEY")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert main(["--provider", "gemini", "--probe-set", str(_probe_set_file(tmp_path))]) == 1


def test_dry_run_still_works_after_the_live_path_lands(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    assert (
        main(["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)])
        == 0
    )


def test_dry_run_under_postgres_mode_off_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_MODE", "off")
    monkeypatch.delenv("PG_TEST_URL", raising=False)
    output = tmp_path / "report.json"
    code = main(
        ["--dry-run", "--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "2.2.0"
    assert report["probe_set_id"] == "unit"


def test_dry_run_with_custom_provider_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_MODE", "off")
    monkeypatch.delenv("PG_TEST_URL", raising=False)
    output = tmp_path / "report.json"
    code = main(
        [
            "--dry-run",
            "--provider",
            "openrouter",
            "--probe-set",
            str(_probe_set_file(tmp_path)),
            "--output",
            str(output),
        ]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["provider"] == "dry-run"


def test_max_consecutive_provider_failures_cli_rejects_below_one(tmp_path: Path) -> None:
    result = run_cli(
        "evaluate_memory",
        "--dry-run",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--max-consecutive-provider-failures",
        "0",
    )
    assert result.returncode == 2


def test_max_consecutive_provider_failures_cli_accepts_positive(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    result = run_cli(
        "evaluate_memory",
        "--dry-run",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
        "--max-consecutive-provider-failures",
        "3",
    )
    assert result.returncode == 0


def test_resolve_max_consecutive_provider_failures_precedence() -> None:
    from scripts.evaluate_memory import _resolve_max_consecutive_provider_failures

    assert _resolve_max_consecutive_provider_failures(None, {}) == 3
    assert (
        _resolve_max_consecutive_provider_failures(
            None, {"MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES": "5"}
        )
        == 5
    )
    assert (
        _resolve_max_consecutive_provider_failures(
            7, {"MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES": "5"}
        )
        == 7
    )


def test_resolve_max_consecutive_provider_failures_rejects_invalid() -> None:
    from scripts.evaluate_memory import _resolve_max_consecutive_provider_failures

    with pytest.raises(ValueError):
        _resolve_max_consecutive_provider_failures(0, {})
    with pytest.raises(ValueError):
        _resolve_max_consecutive_provider_failures(
            None, {"MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES": "0"}
        )
    with pytest.raises(ValueError):
        _resolve_max_consecutive_provider_failures(
            None, {"MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES": "nope"}
        )


def test_run_live_passes_max_consecutive_into_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
    from cowork_agent.features.ai_chat.memory_eval.live_execution import MemoryShardResult
    from cowork_agent.features.ai_chat.memory_eval.probes import load_probe_set
    from scripts.evaluate_memory import run_live

    captured: dict[str, int] = {}

    async def fake_execute(probe_set, env, reply, **kwargs):
        del probe_set, env, reply
        captured["max"] = kwargs["max_consecutive_provider_failures"]
        return MemoryShardResult((), (), (), "nonce", ("aborted: test",), True, "nonce")

    monkeypatch.setattr("scripts.evaluate_memory.execute_memory_shard", fake_execute)
    payload = json.loads(_probe_set_file(tmp_path).read_text(encoding="utf-8"))
    probe_set = load_probe_set(payload)
    env = LiveEnvironment(None, None, True, False, "")
    asyncio.run(
        run_live(
            probe_set,
            env,
            object(),
            provider="gemini",
            model="m",
            max_consecutive_provider_failures=5,
        )
    )
    assert captured["max"] == 5


def test_run_live_delegates_one_full_shard_to_the_live_execution_seam(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
    from cowork_agent.features.ai_chat.memory_eval.live_execution import MemoryShardResult
    from cowork_agent.features.ai_chat.memory_eval.probes import ProbeTest, load_probe_set
    from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow
    from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
    from scripts import evaluate_memory

    payload = json.loads(_probe_set_file(tmp_path).read_text(encoding="utf-8"))
    probe_set = load_probe_set(payload)
    calls: list[tuple[object, ...]] = []
    row = ProbeRow(
        probe_id="st_recall_01",
        targets=MemoryType.SHORT_TERM,
        test=ProbeTest.RECALL,
        full=Outcome.PASS,
        ablated=Outcome.MISS,
        control=Outcome.MISS,
        certain=True,
        latency_ms=3,
    )

    async def execute(*args: object, **kwargs: object) -> MemoryShardResult:
        calls.append((*args, kwargs))
        return MemoryShardResult((row,), ("seed",), (), "nonce", ("seed",), True, "nonce")

    monkeypatch.setattr(evaluate_memory, "execute_memory_shard", execute)
    report = asyncio.run(
        evaluate_memory.run_live(
            probe_set,
            LiveEnvironment(None, None, True, True, ""),
            object(),
            provider="provider",
            model="model",
            max_consecutive_provider_failures=5,
        )
    )

    assert len(calls) == 1
    assert calls[0][0] is probe_set
    assert calls[0][-1]["max_consecutive_provider_failures"] == 5
    assert report["nonce"] == "nonce"
    assert report["seed_failures"] == ["seed"]


def test_run_live_partial_flush_stamps_aborted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
    from cowork_agent.features.ai_chat.memory_eval.live_execution import MemoryShardResult
    from cowork_agent.features.ai_chat.memory_eval.probes import load_probe_set
    from scripts.evaluate_memory import run_live

    async def fake_execute(probe_set, env, reply, **kwargs):
        del probe_set, env, reply, kwargs
        return MemoryShardResult(
            (),
            ("down",),
            (
                {
                    "probe": "st_recall_01",
                    "arm": "full",
                    "question": "what did I say?",
                    "reply": "partial",
                },
            ),
            "nonce",
            ("aborted: tripped",),
            True,
            "nonce",
        )

    monkeypatch.setattr("scripts.evaluate_memory.execute_memory_shard", fake_execute)
    payload = json.loads(_probe_set_file(tmp_path).read_text(encoding="utf-8"))
    probe_set = load_probe_set(payload)
    env = LiveEnvironment(None, None, True, False, "")
    transcript: list[dict[str, object]] = []
    report = asyncio.run(
        run_live(
            probe_set,
            env,
            object(),
            provider="gemini",
            model="m",
            transcript=transcript,
        )
    )
    assert report["aborted"] is True
    assert transcript


def test_run_live_keeps_partial_private_transcript_when_shard_execution_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
    from cowork_agent.features.ai_chat.memory_eval.probes import load_probe_set
    from scripts.evaluate_memory import run_live

    async def fail_after_recording(*args: object, **kwargs: object) -> object:
        del args
        sink = kwargs["private_transcript_sink"]
        assert isinstance(sink, list)
        sink.append({"question": "private question", "reply": "partial reply"})
        raise RuntimeError("ordinary failure")

    monkeypatch.setattr("scripts.evaluate_memory.execute_memory_shard", fail_after_recording)
    payload = json.loads(_probe_set_file(tmp_path).read_text(encoding="utf-8"))
    probe_set = load_probe_set(payload)
    transcript: list[dict[str, object]] = []

    with pytest.raises(RuntimeError, match="ordinary failure"):
        asyncio.run(
            run_live(
                probe_set,
                LiveEnvironment(None, None, True, False, ""),
                object(),
                provider="gemini",
                model="m",
                transcript=transcript,
            )
        )

    assert transcript == [{"question": "private question", "reply": "partial reply"}]


@pytest.mark.parametrize(
    ("error_type", "message"),
    [(RuntimeError, "ordinary failure"), (asyncio.CancelledError, "")],
    ids=("ordinary-failure", "cancellation"),
)
def test_main_writes_no_public_artifact_when_live_execution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
    message: str,
) -> None:
    from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setattr(
        "scripts.evaluate_memory.probe_environment",
        lambda environ: LiveEnvironment(None, None, True, False, ""),
    )
    monkeypatch.setattr(
        "scripts.evaluate_memory._build_chat_reply",
        lambda provider, environ, model=None: (object(), provider, "model-x"),
    )
    captured_transcripts: list[list[dict[str, object]]] = []

    async def fail_run_live(probe_set, env, reply, *, transcript, **kwargs):
        del probe_set, env, reply, kwargs
        captured_transcripts.append(transcript)
        transcript.append({"question": "private question", "reply": "private reply"})
        if message:
            raise error_type(message)
        raise error_type()

    monkeypatch.setattr("scripts.evaluate_memory.run_live", fail_run_live)
    detail_dir = tmp_path / "runs"
    monkeypatch.setattr("scripts.evaluate_memory._DETAIL_DIR", detail_dir)
    output = tmp_path / "report.json"

    with pytest.raises(error_type, match=message or None):
        main(["--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)])

    assert captured_transcripts == [
        [{"question": "private question", "reply": "private reply"}]
    ]
    assert not output.exists()
    assert not list(detail_dir.glob("*.json"))


def test_aborted_run_writes_baseline_and_detail_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setattr(
        "scripts.evaluate_memory.probe_environment",
        lambda environ: LiveEnvironment(None, None, True, False, ""),
    )
    monkeypatch.setattr(
        "scripts.evaluate_memory._build_chat_reply",
        lambda provider, environ, model=None: (object(), provider, "model-x"),
    )

    async def fake_run_live(probe_set, env, reply, *, provider, model, transcript, **kwargs):
        del probe_set, env, reply, provider, kwargs
        transcript.append(
            {
                "probe": "st_recall_01",
                "arm": "full",
                "reply": "partial",
                "question": "what did I say?",
            }
        )
        return {
            "schema_version": "2.1.0",
            "probe_set_id": "unit",
            "aborted": True,
            "run_key": "rk",
            "nonce": "n",
            "model": model,
            "ran_at": "2026-01-01T00:00:00+00:00",
            "seed_failures": ["down"],
        }

    monkeypatch.setattr("scripts.evaluate_memory.run_live", fake_run_live)
    detail_dir = tmp_path / "runs"
    monkeypatch.setattr("scripts.evaluate_memory._DETAIL_DIR", detail_dir)
    output = tmp_path / "report.json"
    code = main(["--probe-set", str(_probe_set_file(tmp_path)), "--output", str(output)])
    assert code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["aborted"] is True
    matches = list(detail_dir.glob("*-unit-detail.json"))
    assert matches
    detail = json.loads(matches[-1].read_text(encoding="utf-8"))
    assert detail["arms"]
