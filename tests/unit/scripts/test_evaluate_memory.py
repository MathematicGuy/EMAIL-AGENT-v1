from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec
from cowork_agent.features.ai_chat.memory_eval.runner import run_key
from cowork_agent.features.batch_evaluation.contracts import (
    EvaluationRequest,
    EvaluationWarning,
    JobState,
    UnitState,
)
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


class _FakeEvaluationService:
    def __init__(
        self,
        requests: list[EvaluationRequest],
        idempotency_keys: list[str],
        job: SimpleNamespace,
        report: dict[str, object],
        submit_error: Exception | None = None,
    ) -> None:
        self._requests = requests
        self._idempotency_keys = idempotency_keys
        self._job = job
        self._report = report
        self._submit_error = submit_error

    async def submit(
        self, request: EvaluationRequest, *, idempotency_key: str
    ) -> SimpleNamespace:
        # Raised before anything is recorded: a rejected submission must not
        # start (or bill) any evaluation work.
        if self._submit_error is not None:
            raise self._submit_error
        self._requests.append(request)
        self._idempotency_keys.append(idempotency_key)
        return self._job

    async def get_result(self, job_id: str) -> dict[str, object]:
        assert job_id == self._job.job_id
        return self._report


def _unit_snapshot(*states: UnitState) -> tuple[SimpleNamespace, ...]:
    """One durable unit-progress snapshot, shaped for the watchdog's read."""

    return tuple(
        SimpleNamespace(unit_id=f"unit-{index}", state=state)
        for index, state in enumerate(states)
    )


def _advancing_unit_snapshots(count: int) -> list[tuple[SimpleNamespace, ...]]:
    """`count` pairwise-different snapshots, each moving one unit forward.

    Enough distinct snapshots let a poll outlast the idle bound while always
    showing fresh durable movement - exactly the shape of a healthy RUNNING
    phase, where only unit rows change.
    """

    snapshots: list[tuple[SimpleNamespace, ...]] = []
    states = [UnitState.READY] * 4
    index = 0
    for _ in range(count):
        snapshots.append(_unit_snapshot(*states))
        if states[index] is UnitState.READY:
            states[index] = UnitState.RUNNING
        elif states[index] is UnitState.RUNNING:
            states[index] = UnitState.SUCCEEDED
            index = (index + 1) % len(states)
    return snapshots


class _FakeEvaluationRepository:
    def __init__(
        self,
        job: SimpleNamespace,
        poll_sequence: Sequence[SimpleNamespace] | None = None,
        unit_snapshots: Sequence[tuple[SimpleNamespace, ...]] | None = None,
    ) -> None:
        self._job = job
        self._pending = list(poll_sequence or ())
        self._unit_snapshots = list(unit_snapshots or ())
        self._last_units: tuple[SimpleNamespace, ...] = ()
        self.polls = 0

    async def get_job(self, job_id: str) -> SimpleNamespace:
        assert job_id == self._job.job_id
        self.polls += 1
        if self._pending:
            return self._pending.pop(0)
        return self._job

    async def list_units(self, job_id: str) -> tuple[SimpleNamespace, ...]:
        assert job_id == self._job.job_id
        if self._unit_snapshots:
            self._last_units = self._unit_snapshots.pop(0)
        return self._last_units


class _FakeEvaluationRuntime:
    def __init__(
        self,
        requests: list[EvaluationRequest],
        idempotency_keys: list[str],
        *,
        effective_workers: int,
        healthy_workers: int,
        state: JobState = JobState.SUCCEEDED,
        warnings: tuple[EvaluationWarning, ...] = (),
        result_manifest: dict[str, object] | None = None,
        poll_sequence: Sequence[SimpleNamespace] | None = None,
        submit_error: Exception | None = None,
        unit_snapshots: Sequence[tuple[SimpleNamespace, ...]] | None = None,
    ) -> None:
        self.job = SimpleNamespace(
            job_id="memory-job-1",
            state=state,
            effective_workers=effective_workers,
            warnings=warnings,
            updated_at=datetime.now(UTC),
        )
        self.service = _FakeEvaluationService(
            requests,
            idempotency_keys,
            self.job,
            result_manifest
            if result_manifest is not None
            else {
                "schema_version": "2.2.0",
                "probe_set_id": "unit",
                "provider": "mistral",
                "model": "mistral-small-2603",
                "aborted": state is not JobState.SUCCEEDED,
                "execution_manifest": {"private_runtime_metadata": "not for baseline"},
            },
            submit_error=submit_error,
        )
        self.repository = _FakeEvaluationRepository(self.job, poll_sequence, unit_snapshots)
        self.credential_pool = SimpleNamespace(healthy_count=healthy_workers)
        self.initialized = False
        self.recovered = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def recover(self) -> None:
        self.recovered = True

    async def close(self) -> None:
        self.closed = True


def _install_fake_mistral_runtime(
    monkeypatch: pytest.MonkeyPatch,
    requests: list[EvaluationRequest],
    idempotency_keys: list[str],
    *,
    effective_workers: int,
    healthy_workers: int,
    state: JobState = JobState.SUCCEEDED,
    warnings: tuple[EvaluationWarning, ...] = (),
    result_manifest: dict[str, object] | None = None,
    poll_sequence: Sequence[SimpleNamespace] | None = None,
    submit_error: Exception | None = None,
    unit_snapshots: Sequence[tuple[SimpleNamespace, ...]] | None = None,
) -> _FakeEvaluationRuntime:
    from tests.unit.scripts.cli_harness import load_script

    runtime = _FakeEvaluationRuntime(
        requests,
        idempotency_keys,
        effective_workers=effective_workers,
        healthy_workers=healthy_workers,
        state=state,
        warnings=warnings,
        result_manifest=result_manifest,
        poll_sequence=poll_sequence,
        submit_error=submit_error,
        unit_snapshots=unit_snapshots,
    )
    script = load_script("evaluate_memory")
    monkeypatch.setattr(
        script,
        "build_evaluation_runtime",
        lambda config, environ: runtime,
        raising=False,
    )
    return runtime


def _keep_legacy_mistral_path_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep pre-Task-9 code deterministic while proving it never uses this path."""

    from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
    from tests.unit.scripts.cli_harness import load_script

    script = load_script("evaluate_memory")
    monkeypatch.setattr(
        script,
        "probe_environment",
        lambda environ: LiveEnvironment(None, None, True, False, ""),
    )
    monkeypatch.setattr(
        script,
        "_build_chat_reply",
        lambda provider, environ, model=None: (object(), provider, "mistral-small-2603"),
    )

    async def legacy_report(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "schema_version": "2.2.0",
            "probe_set_id": "unit",
            "provider": "mistral",
            "model": "mistral-small-2603",
            "aborted": False,
        }

    monkeypatch.setattr(script, "run_live", legacy_report)
    monkeypatch.setattr(script, "run_with_selector_loop", lambda coroutine: asyncio.run(coroutine))


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


def test_mistral_max_workers_defaults_to_one_and_keeps_runtime_metadata_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    _keep_legacy_mistral_path_offline(monkeypatch)
    runtime = _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
    )
    output = tmp_path / "report.json"

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
    )

    assert result.returncode == 0
    assert requests[0].max_workers == 1
    assert runtime.initialized is True
    assert runtime.closed is True
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "2.2.0"
    assert "execution_manifest" not in report
    assert "not for baseline" not in output.read_text(encoding="utf-8")


def test_four_requested_mistral_workers_with_three_keys_reports_reduction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key-1")
    monkeypatch.setenv("MISTRAL_API_KEY2", "test-mistral-key-2")
    monkeypatch.setenv("MISTRAL_API_KEY3", "test-mistral-key-3")
    _keep_legacy_mistral_path_offline(monkeypatch)
    _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=3,
        healthy_workers=3,
        warnings=(
            EvaluationWarning(
                code="WORKER_COUNT_REDUCED",
                details={
                    "requested_workers": 4,
                    "effective_workers": 3,
                    "healthy_credentials": 3,
                },
            ),
        ),
    )

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--max-workers",
        "4",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(tmp_path / "report.json"),
    )

    assert result.returncode == 0
    assert requests[0].max_workers == 4
    assert "WORKER_COUNT_REDUCED" in result.stderr
    assert "requested_workers=4" in result.stderr
    assert "effective_workers=3" in result.stderr
    assert "healthy_credentials=3" in result.stderr


def test_ready_work_scarcity_does_not_emit_a_credential_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key-1")
    monkeypatch.setenv("MISTRAL_API_KEY2", "test-mistral-key-2")
    monkeypatch.setenv("MISTRAL_API_KEY3", "test-mistral-key-3")
    _keep_legacy_mistral_path_offline(monkeypatch)
    _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=3,
    )

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--max-workers",
        "4",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(tmp_path / "report.json"),
    )

    assert result.returncode == 0
    assert "WORKER_COUNT_REDUCED" not in result.stderr


def test_mistral_idempotency_key_is_replayed_without_a_new_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    _keep_legacy_mistral_path_offline(monkeypatch)
    _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
    )
    argv = (
        "--provider",
        "mistral",
        "--idempotency-key",
        "replay-memory-evaluation",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(tmp_path / "report.json"),
    )

    first = run_cli("evaluate_memory", *argv)
    replay = run_cli("evaluate_memory", *argv)

    assert first.returncode == replay.returncode == 0
    assert len(requests) == 2
    assert idempotency_keys == ["replay-memory-evaluation", "replay-memory-evaluation"]


def test_stranded_replayed_job_is_recovered_and_reaches_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A replay against a job stranded in QUEUED must recover it and poll past
    # the non-terminal snapshot instead of returning the stranded state.
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    _keep_legacy_mistral_path_offline(monkeypatch)
    stranded = SimpleNamespace(
        job_id="memory-job-1",
        state=JobState.QUEUED,
        updated_at=datetime.now(UTC),
    )
    runtime = _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
        poll_sequence=(stranded,),
    )
    output = tmp_path / "report.json"

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--idempotency-key",
        "replay-stranded-job",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
    )

    assert result.returncode == 0
    assert runtime.initialized is True
    assert runtime.recovered is True
    assert runtime.repository.polls >= 2
    assert runtime.closed is True
    assert output.exists()


def test_stranded_job_without_progress_fails_cleanly_instead_of_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A job recovery cannot drive (e.g. stranded in VALIDATING/ACCEPTED) never
    # updates; the wait must be bounded and fail cleanly instead of looping
    # forever.
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    monkeypatch.setenv("MEMEVAL_JOB_WAIT_IDLE_SECONDS", "0.2")
    _keep_legacy_mistral_path_offline(monkeypatch)
    runtime = _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
        state=JobState.QUEUED,
    )
    output = tmp_path / "report.json"

    started = time.monotonic()
    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--idempotency-key",
        "replay-stuck-job",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 10  # bounded; the old loop never returned
    assert result.returncode == 1
    assert "no progress" in result.stderr
    assert runtime.closed is True
    assert not output.exists()


def test_running_job_with_advancing_unit_progress_survives_the_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A healthy RUNNING phase changes neither state nor updated_at; only the
    # durable unit rows move. Advancing units must count as progress, or the
    # watchdog would cancel billed in-flight work on every long run. The poll
    # sequence outlasts the idle bound, so only unit movement keeps it alive.
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    monkeypatch.setenv("MEMEVAL_JOB_WAIT_IDLE_SECONDS", "0.3")
    _keep_legacy_mistral_path_offline(monkeypatch)
    frozen_updated_at = datetime.now(UTC)
    running = SimpleNamespace(
        job_id="memory-job-1",
        state=JobState.RUNNING,
        updated_at=frozen_updated_at,
    )
    runtime = _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
        poll_sequence=(running,) * 8,
        unit_snapshots=_advancing_unit_snapshots(8),
    )
    output = tmp_path / "report.json"

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
    )

    assert result.returncode == 0
    assert "no progress" not in result.stderr
    assert runtime.repository.polls >= 8
    assert runtime.closed is True
    assert output.exists()


def test_stalled_running_job_with_frozen_units_fails_within_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Units that never move are the real stalled signal: the watchdog must
    # still fail cleanly within the idle bound.
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    monkeypatch.setenv("MEMEVAL_JOB_WAIT_IDLE_SECONDS", "0.2")
    _keep_legacy_mistral_path_offline(monkeypatch)
    runtime = _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
        state=JobState.RUNNING,
        unit_snapshots=(_unit_snapshot(UnitState.READY, UnitState.READY),),
    )
    output = tmp_path / "report.json"

    started = time.monotonic()
    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 10
    assert result.returncode == 1
    assert "no progress" in result.stderr
    assert runtime.closed is True
    assert not output.exists()


def test_invalid_job_wait_idle_env_exits_two_before_any_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A malformed idle bound is invalid configuration: reject it before submit
    # so no evaluation work (or spend) can start, exit 2 like other bad flags.
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    monkeypatch.setenv("MEMEVAL_JOB_WAIT_IDLE_SECONDS", "bogus")
    _keep_legacy_mistral_path_offline(monkeypatch)
    runtime = _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
    )

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(tmp_path / "report.json"),
    )

    assert result.returncode == 2
    assert "MEMEVAL_JOB_WAIT_IDLE_SECONDS" in result.stderr
    assert requests == []
    assert runtime.initialized is False


@pytest.mark.parametrize("stub_manifest", [{"state": "failed"}, {"state": "cancelled"}])
def test_stub_manifest_is_not_written_as_a_baseline(
    stub_manifest: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On unplanned failure the runner writes a bare state stub, which is not a
    # memory-eval report and must not be committed under baselines/.
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    _keep_legacy_mistral_path_offline(monkeypatch)
    _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
        state=JobState.FAILED,
        result_manifest=dict(stub_manifest),
    )
    output = tmp_path / "report.json"

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
    )

    assert result.returncode == 1
    assert "no scorable report" in result.stderr
    assert not output.exists()
    assert result.stdout.strip() == ""


def test_zero_healthy_mistral_keys_exits_one_without_spending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With no healthy credential the service rejects the submission before any
    # work starts: clean exit 1, surfaced reason, and no baseline artifact.
    from cowork_agent.features.batch_evaluation.service import EvaluationValidationError

    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    _keep_legacy_mistral_path_offline(monkeypatch)
    runtime = _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=0,
        healthy_workers=0,
        submit_error=EvaluationValidationError(
            "no compatible evaluation workers are available"
        ),
    )
    output = tmp_path / "report.json"

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(output),
    )

    assert result.returncode == 1
    assert "no compatible evaluation workers are available" in result.stderr
    assert requests == []
    assert runtime.closed is True
    assert not output.exists()


@pytest.mark.parametrize("state", (JobState.FAILED, JobState.CANCELLED))
def test_mistral_terminal_failure_or_cancellation_returns_nonzero(
    state: JobState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[EvaluationRequest] = []
    idempotency_keys: list[str] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    _keep_legacy_mistral_path_offline(monkeypatch)
    _install_fake_mistral_runtime(
        monkeypatch,
        requests,
        idempotency_keys,
        effective_workers=1,
        healthy_workers=1,
        state=state,
    )

    result = run_cli(
        "evaluate_memory",
        "--provider",
        "mistral",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--output",
        str(tmp_path / "report.json"),
    )

    assert result.returncode == 1


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
