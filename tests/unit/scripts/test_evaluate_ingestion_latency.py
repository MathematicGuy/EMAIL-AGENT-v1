from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "evaluate_ingestion_latency.py"


def _module():
    return load_script("evaluate_ingestion_latency")


def _sample(**overrides: object) -> dict[str, object]:
    sample: dict[str, object] = {
        "scenario": "small-pdf",
        "fixture_id": "dang-ky-xe-pdf-v1",
        "media_type": "application/pdf",
        "bytes": 12_000,
        "pages": 2,
        "chunks": 3,
        "snapshot_bytes": 4_000,
        "database_host_class": "loopback",
        "storage_provider": "local",
        "embedding_provider": "fixture",
        "status": "ready",
        "retrieval_verified": True,
        "metrics_ms": {
            "hash": 10,
            "initiate": 20,
            "signed_put": 30,
            "complete": 40,
            "attach_to_server_ready": 50,
            "server_ready_to_ui_ready": 60,
            "attach_to_ready": 110,
            "send_to_first_token": 70,
            "send_to_complete": 80,
            "queue_delay": 90,
            "worker_execution": 100,
            "source_download": 11,
            "extraction_chunking": 12,
            "chunk_persistence": 13,
            "embedding": 14,
            "local_index_update": 15,
            "snapshot_upload": 16,
            "ready_transition": 17,
        },
    }
    sample.update(overrides)
    return sample


def test_aggregates_every_metric_with_repository_nearest_rank_percentiles() -> None:
    module = _module()
    samples = []
    for index in range(1, 21):
        metrics = dict(_sample()["metrics_ms"])
        metrics["hash"] = index
        samples.append(module.parse_samples({"samples": [_sample(metrics_ms=metrics)]})[0])

    report = module.compute_report(samples, expect_local=False)

    assert set(report["metrics_ms"]) == set(module.METRIC_KEYS)
    assert report["metrics_ms"]["hash"] == {
        "count": 20,
        "min": 1,
        "p50": 10,
        "p95": 19,
        "max": 20,
    }


def test_aggregates_backend_metrics_and_preserves_nulls() -> None:
    module = _module()
    backend_metrics = {
        "queue_delay": 11,
        "worker_execution": 12,
        "source_download": 13,
        "extraction_chunking": 14,
        "chunk_persistence": 15,
        "embedding": 16,
        "local_index_update": 17,
        "snapshot_upload": None,
        "ready_transition": 19,
    }
    metrics = {**_sample()["metrics_ms"], **backend_metrics}

    samples = module.parse_samples({"samples": [_sample(metrics_ms=metrics)]})
    report = module.compute_report(samples, expect_local=False)

    assert set(backend_metrics).issubset(module.METRIC_KEYS)
    assert report["metrics_ms"]["queue_delay"] == {
        "count": 1,
        "min": 11,
        "p50": 11,
        "p95": 11,
        "max": 11,
    }
    assert report["metrics_ms"]["snapshot_upload"] == {
        "count": 0,
        "min": None,
        "p50": None,
        "p95": None,
        "max": None,
    }


def test_missing_metrics_and_failed_samples_are_recorded_without_zero_filling() -> None:
    module = _module()
    incomplete_metrics = dict(_sample()["metrics_ms"])
    incomplete_metrics.pop("send_to_complete")
    failed_metrics = {"hash": 15, "initiate": None}
    samples = module.parse_samples(
        {
            "samples": [
                _sample(metrics_ms=incomplete_metrics),
                _sample(
                    scenario="docx",
                    fixture_id="law-31-2024-docx-v1",
                    media_type=(
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    status="failed",
                    retrieval_verified=False,
                    metrics_ms=failed_metrics,
                ),
            ]
        }
    )

    report = module.compute_report(samples, expect_local=False)

    assert report["summary"] == {
        "complete_sample_count": 0,
        "incomplete_sample_count": 1,
        "failed_sample_count": 1,
        "retrieval_verified_count": 1,
        "status_counts": {"failed": 1, "ready": 1},
    }
    assert report["metrics_ms"]["send_to_complete"] == {
        "count": 0,
        "min": None,
        "p50": None,
        "p95": None,
        "max": None,
    }
    assert report["samples"][1]["missing_metrics"] == [
        key
        for key in module.METRIC_KEYS
        if key not in failed_metrics or failed_metrics[key] is None
    ]
    assert "metrics_ms" not in report["samples"][1]


@pytest.mark.parametrize("snapshot", [None, pytest.param("missing", id="omitted")])
def test_snapshot_bytes_is_optional_observational_metadata(snapshot: object) -> None:
    raw = _sample(snapshot_bytes=snapshot)
    if snapshot == "missing":
        raw.pop("snapshot_bytes")

    parsed = _module().parse_samples({"samples": [raw]})

    assert parsed[0].snapshot_bytes is None


@pytest.mark.parametrize("snapshot", [-1, 1.5, True, "100"])
def test_snapshot_bytes_is_a_nonnegative_integer_when_present(snapshot: object) -> None:
    with pytest.raises(ValueError, match="snapshot_bytes"):
        _module().parse_samples({"samples": [_sample(snapshot_bytes=snapshot)]})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_text", "secret document"),
        ("question", "secret question"),
        ("answer", "secret answer"),
        ("prompt", "secret prompt"),
        ("signed_url", "https://storage.invalid/secret"),
        ("cookies", "session=secret"),
        ("credentials", "secret"),
        ("document_id", "backend-correlation-id"),
        ("retrieved_chunk_content", "secret chunk"),
        ("unexpected", "anything"),
    ],
)
def test_rejects_sensitive_and_unknown_sample_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="unknown sample fields"):
        _module().parse_samples({"samples": [_sample(**{field: value})]})


def test_rejects_unknown_metric_keys() -> None:
    metrics = dict(_sample()["metrics_ms"])
    metrics["raw_response"] = "secret"

    with pytest.raises(ValueError, match="unknown metrics_ms fields"):
        _module().parse_samples({"samples": [_sample(metrics_ms=metrics)]})


@pytest.mark.parametrize("host_class", ["localhost", "cloud", ""])
def test_environment_classification_accepts_only_loopback_or_remote(host_class: object) -> None:
    with pytest.raises(ValueError, match="database_host_class"):
        _module().parse_samples({"samples": [_sample(database_host_class=host_class)]})


@pytest.mark.parametrize("field", ["storage_provider", "embedding_provider"])
@pytest.mark.parametrize("value", ["", 0, False, [], {}])
def test_provider_metadata_accepts_only_nonempty_strings_or_null(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match=field):
        _module().parse_samples({"samples": [_sample(**{field: value})]})


def test_failed_sample_preserves_null_environment_metadata_without_fabrication() -> None:
    module = _module()
    samples = module.parse_samples(
        {
            "samples": [
                _sample(
                    status="failed",
                    retrieval_verified=False,
                    database_host_class=None,
                    storage_provider=None,
                    embedding_provider=None,
                )
            ]
        }
    )

    report = module.compute_report(samples, expect_local=False)

    assert report["summary"] == {
        "complete_sample_count": 0,
        "incomplete_sample_count": 0,
        "failed_sample_count": 1,
        "retrieval_verified_count": 0,
        "status_counts": {"failed": 1},
    }
    assert report["environment"]["database_host_classes"] == []
    assert report["samples"][0]["database_host_class"] is None
    assert report["samples"][0]["storage_provider"] is None
    assert report["samples"][0]["embedding_provider"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "incomplete"},
        {"retrieval_verified": False},
        {"database_host_class": None},
        {"storage_provider": None},
        {"embedding_provider": None},
    ],
)
def test_only_ready_verified_complete_samples_count_as_complete(
    overrides: dict[str, object],
) -> None:
    module = _module()
    samples = module.parse_samples({"samples": [_sample(**overrides)]})

    report = module.compute_report(samples, expect_local=False)

    assert report["summary"]["complete_sample_count"] == 0
    assert report["summary"]["incomplete_sample_count"] == 1
    assert report["summary"]["failed_sample_count"] == 0


def test_ready_verified_sample_with_metrics_and_environment_counts_as_complete() -> None:
    module = _module()
    samples = module.parse_samples({"samples": [_sample()]})

    report = module.compute_report(samples, expect_local=False)

    assert report["summary"]["complete_sample_count"] == 1
    assert report["summary"]["incomplete_sample_count"] == 0


def test_expect_local_rejects_remote_database_samples() -> None:
    module = _module()
    samples = module.parse_samples(
        {"samples": [_sample(database_host_class="remote", storage_provider="supabase")]}
    )

    with pytest.raises(ValueError, match="expected local.*remote"):
        module.compute_report(samples, expect_local=True)


def test_rejects_mixed_database_host_classes_before_aggregation() -> None:
    module = _module()
    samples = module.parse_samples(
        {
            "samples": [
                _sample(),
                _sample(database_host_class="remote", storage_provider="supabase"),
            ]
        }
    )

    with pytest.raises(ValueError, match="mix loopback and remote"):
        module.compute_report(samples, expect_local=False)


def test_mixed_host_check_ignores_unknown_failed_sample_metadata() -> None:
    module = _module()
    samples = module.parse_samples(
        {
            "samples": [
                _sample(),
                _sample(
                    status="failed",
                    retrieval_verified=False,
                    database_host_class=None,
                    storage_provider=None,
                    embedding_provider=None,
                ),
            ]
        }
    )

    report = module.compute_report(samples, expect_local=False)

    assert report["environment"]["database_host_classes"] == ["loopback"]
    assert report["summary"]["complete_sample_count"] == 1
    assert report["summary"]["failed_sample_count"] == 1


def test_cli_writes_metadata_only_report(tmp_path: Path) -> None:
    source = tmp_path / "raw.json"
    output = tmp_path / "report.json"
    source.write_text(json.dumps({"samples": [_sample()]}), encoding="utf-8")

    result = run_cli(
        "evaluate_ingestion_latency",
        "--input",
        str(source),
        "--output",
        str(output),
        "--expect-local",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "chat-ingestion-latency-report.v1"
    assert report["environment"] == {
        "database_host_classes": ["loopback"],
        "expectation": "local",
    }
    serialized = output.read_text(encoding="utf-8")
    for forbidden in (
        "document_text",
        "question",
        "answer",
        "prompt",
        "signed_url",
        "cookies",
        "credentials",
        "retrieved_chunk_content",
    ):
        assert forbidden not in serialized


def test_help_runs_without_provider_keys() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--expect-local" in result.stdout
