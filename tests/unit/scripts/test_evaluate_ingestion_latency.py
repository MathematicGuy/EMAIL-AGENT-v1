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


def test_ingestion_metrics_aggregation_and_null_preservation() -> None:
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

    # Preserves nulls and failed samples without zero-filling
    failed_metrics = {"hash": 15, "initiate": None}
    mixed_samples = module.parse_samples(
        {
            "samples": [
                _sample(),
                _sample(status="failed", retrieval_verified=False, metrics_ms=failed_metrics),
            ]
        }
    )
    mixed_report = module.compute_report(mixed_samples, expect_local=False)
    assert mixed_report["summary"]["failed_sample_count"] == 1
    assert mixed_report["summary"]["complete_sample_count"] == 1


def test_snapshot_bytes_validation() -> None:
    module = _module()
    assert (
        module.parse_samples({"samples": [_sample(snapshot_bytes=None)]})[0].snapshot_bytes is None
    )
    sample_no_snap = _sample()
    sample_no_snap.pop("snapshot_bytes", None)
    assert module.parse_samples({"samples": [sample_no_snap]})[0].snapshot_bytes is None

    for invalid in (-1, 1.5, True, "100"):
        with pytest.raises(ValueError, match="snapshot_bytes"):
            module.parse_samples({"samples": [_sample(snapshot_bytes=invalid)]})


def test_rejects_sensitive_and_unknown_sample_fields() -> None:
    cases = [
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
    ]
    for field, value in cases:
        with pytest.raises(ValueError, match="unknown sample fields"):
            _module().parse_samples({"samples": [_sample(**{field: value})]})

    metrics = dict(_sample()["metrics_ms"])
    metrics["raw_response"] = "secret"
    with pytest.raises(ValueError, match="unknown metrics_ms fields"):
        _module().parse_samples({"samples": [_sample(metrics_ms=metrics)]})


def test_sample_metadata_validations() -> None:
    module = _module()
    for host_class in ("localhost", "cloud", ""):
        with pytest.raises(ValueError, match="database_host_class"):
            module.parse_samples({"samples": [_sample(database_host_class=host_class)]})

    for field in ("storage_provider", "embedding_provider"):
        for value in ("", 0, False, [], {}):
            with pytest.raises(ValueError, match=field):
                module.parse_samples({"samples": [_sample(**{field: value})]})


def test_sample_completion_and_failure_accounting() -> None:
    module = _module()
    # Ready + verified = complete
    complete_report = module.compute_report(
        module.parse_samples({"samples": [_sample()]}), expect_local=False
    )
    assert complete_report["summary"]["complete_sample_count"] == 1

    # Incomplete variants
    for overrides in [
        {"status": "incomplete"},
        {"retrieval_verified": False},
        {"database_host_class": None},
        {"storage_provider": None},
        {"embedding_provider": None},
    ]:
        report = module.compute_report(
            module.parse_samples({"samples": [_sample(**overrides)]}), expect_local=False
        )
        assert report["summary"]["complete_sample_count"] == 0
        assert report["summary"]["incomplete_sample_count"] == 1


def test_database_host_class_checks_and_expect_local() -> None:
    module = _module()
    remote_samples = module.parse_samples(
        {"samples": [_sample(database_host_class="remote", storage_provider="supabase")]}
    )
    with pytest.raises(ValueError, match="expected local.*remote"):
        module.compute_report(remote_samples, expect_local=True)

    mixed_samples = module.parse_samples(
        {"samples": [_sample(), _sample(database_host_class="remote", storage_provider="supabase")]}
    )
    with pytest.raises(ValueError, match="mix loopback and remote"):
        module.compute_report(mixed_samples, expect_local=False)


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
