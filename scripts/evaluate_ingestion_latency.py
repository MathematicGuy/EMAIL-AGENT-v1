"""Aggregate metadata-only Chat document-ingestion latency samples.

The raw Playwright artifact is local and ephemeral. This evaluator accepts a
strict timing/fixture metadata schema and writes a safe aggregate report; text,
prompts, URLs, credentials, cookies, and retrieved content are rejected as
unknown fields.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "evaluations" / "CHAT" / "ingestion-latency" / "runs"
SCHEMA_VERSION = "chat-ingestion-latency-report.v1"

METRIC_KEYS = (
    "hash",
    "initiate",
    "signed_put",
    "complete",
    "attach_to_server_ready",
    "server_ready_to_ui_ready",
    "attach_to_ready",
    "send_to_first_token",
    "send_to_complete",
    "queue_delay",
    "worker_execution",
    "source_download",
    "extraction_chunking",
    "chunk_persistence",
    "embedding",
    "local_index_update",
    "snapshot_upload",
    "ready_transition",
)
SAMPLE_KEYS = frozenset(
    {
        "scenario",
        "fixture_id",
        "media_type",
        "bytes",
        "pages",
        "chunks",
        "snapshot_bytes",
        "database_host_class",
        "storage_provider",
        "embedding_provider",
        "status",
        "retrieval_verified",
        "metrics_ms",
    }
)
REQUIRED_SAMPLE_KEYS = SAMPLE_KEYS - {"snapshot_bytes"}
DATABASE_HOST_CLASSES = frozenset({"loopback", "remote"})


@dataclass(frozen=True)
class IngestionLatencySample:
    """One validated sample containing only safe metadata and timings."""

    scenario: str
    fixture_id: str
    media_type: str
    bytes: int
    pages: int
    chunks: int
    snapshot_bytes: int | None
    database_host_class: str | None
    storage_provider: str | None
    embedding_provider: str | None
    status: str
    retrieval_verified: bool
    metrics_ms: dict[str, int | float | None]

    @property
    def missing_metrics(self) -> list[str]:
        return [key for key in METRIC_KEYS if self.metrics_ms.get(key) is None]

    def safe_metadata(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "fixture_id": self.fixture_id,
            "media_type": self.media_type,
            "bytes": self.bytes,
            "pages": self.pages,
            "chunks": self.chunks,
            "snapshot_bytes": self.snapshot_bytes,
            "database_host_class": self.database_host_class,
            "storage_provider": self.storage_provider,
            "embedding_provider": self.embedding_provider,
            "status": self.status,
            "retrieval_verified": self.retrieval_verified,
            "missing_metrics": self.missing_metrics,
        }


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_non_empty_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, field)


def _non_negative_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _optional_non_negative_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _non_negative_integer(value, field)


def _metric(value: object, field: str) -> int | float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"metrics_ms.{field} must be a non-negative finite number or null")
    return value


def parse_samples(payload: Mapping[str, object]) -> tuple[IngestionLatencySample, ...]:
    unknown_root = set(payload) - {"samples"}
    if unknown_root:
        raise ValueError(f"unknown root fields: {', '.join(sorted(unknown_root))}")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("samples must be a non-empty list")

    samples: list[IngestionLatencySample] = []
    for index, raw_sample in enumerate(raw_samples):
        if not isinstance(raw_sample, dict):
            raise ValueError(f"samples[{index}] must be an object")
        unknown_sample = set(raw_sample) - SAMPLE_KEYS
        if unknown_sample:
            raise ValueError(f"unknown sample fields: {', '.join(sorted(unknown_sample))}")
        missing_sample = REQUIRED_SAMPLE_KEYS - set(raw_sample)
        if missing_sample:
            raise ValueError(f"missing sample fields: {', '.join(sorted(missing_sample))}")

        raw_metrics = raw_sample["metrics_ms"]
        if not isinstance(raw_metrics, dict):
            raise ValueError("metrics_ms must be an object")
        unknown_metrics = set(raw_metrics) - set(METRIC_KEYS)
        if unknown_metrics:
            raise ValueError(f"unknown metrics_ms fields: {', '.join(sorted(unknown_metrics))}")

        host_class = _optional_non_empty_string(
            raw_sample["database_host_class"], "database_host_class"
        )
        if host_class is not None and host_class not in DATABASE_HOST_CLASSES:
            raise ValueError("database_host_class must be loopback, remote, or null")
        retrieval_verified = raw_sample["retrieval_verified"]
        if not isinstance(retrieval_verified, bool):
            raise ValueError("retrieval_verified must be true or false")

        samples.append(
            IngestionLatencySample(
                scenario=_non_empty_string(raw_sample["scenario"], "scenario"),
                fixture_id=_non_empty_string(raw_sample["fixture_id"], "fixture_id"),
                media_type=_non_empty_string(raw_sample["media_type"], "media_type"),
                bytes=_non_negative_integer(raw_sample["bytes"], "bytes"),
                pages=_non_negative_integer(raw_sample["pages"], "pages"),
                chunks=_non_negative_integer(raw_sample["chunks"], "chunks"),
                snapshot_bytes=_optional_non_negative_integer(
                    raw_sample.get("snapshot_bytes"), "snapshot_bytes"
                ),
                database_host_class=host_class,
                storage_provider=_optional_non_empty_string(
                    raw_sample["storage_provider"], "storage_provider"
                ),
                embedding_provider=_optional_non_empty_string(
                    raw_sample["embedding_provider"], "embedding_provider"
                ),
                status=_non_empty_string(raw_sample["status"], "status"),
                retrieval_verified=retrieval_verified,
                metrics_ms={key: _metric(raw_metrics.get(key), key) for key in METRIC_KEYS},
            )
        )
    return tuple(samples)


def _percentile(sorted_values: Sequence[int | float], percentile: int) -> int | float:
    """Return the repository-standard nearest-rank percentile."""
    position = math.ceil(percentile / 100 * len(sorted_values)) - 1
    return sorted_values[min(max(position, 0), len(sorted_values) - 1)]


def _metric_summary(values: Sequence[int | float]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None}
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "max": ordered[-1],
    }


def _is_complete_sample(sample: IngestionLatencySample) -> bool:
    return (
        sample.status == "ready"
        and sample.retrieval_verified
        and not sample.missing_metrics
        and sample.database_host_class is not None
        and sample.storage_provider is not None
        and sample.embedding_provider is not None
    )


def compute_report(
    samples: Sequence[IngestionLatencySample], *, expect_local: bool
) -> dict[str, object]:
    database_host_classes = {
        sample.database_host_class for sample in samples if sample.database_host_class is not None
    }
    if len(database_host_classes) > 1:
        raise ValueError("cannot mix loopback and remote database_host_class samples in one report")
    remote_samples = [
        sample.fixture_id for sample in samples if sample.database_host_class == "remote"
    ]
    if expect_local and remote_samples:
        raise ValueError(
            "expected local database samples but found remote database_host_class for: "
            + ", ".join(remote_samples)
        )

    failed = [sample for sample in samples if sample.status.casefold() == "failed"]
    complete = [sample for sample in samples if _is_complete_sample(sample)]
    incomplete = [
        sample
        for sample in samples
        if sample.status.casefold() != "failed" and not _is_complete_sample(sample)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_count": len(samples),
        "environment": {
            "database_host_classes": sorted(database_host_classes),
            "expectation": "local" if expect_local else "any",
        },
        "summary": {
            "complete_sample_count": len(complete),
            "incomplete_sample_count": len(incomplete),
            "failed_sample_count": len(failed),
            "retrieval_verified_count": sum(sample.retrieval_verified for sample in samples),
            "status_counts": dict(sorted(Counter(sample.status for sample in samples).items())),
        },
        "metrics_ms": {
            key: _metric_summary(
                [value for sample in samples if (value := sample.metrics_ms[key]) is not None]
            )
            for key in METRIC_KEYS
        },
        "samples": [sample.safe_metadata() for sample in samples],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Raw Playwright JSON")
    parser.add_argument("--output", type=Path, help="Metadata-only report path")
    parser.add_argument(
        "--expect-local",
        action="store_true",
        help="Reject samples classified as a remote database host",
    )
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input root must be an object")
        samples = parse_samples(payload)
        report = compute_report(samples, expect_local=args.expect_local)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))

    target = args.output or DEFAULT_OUTPUT_DIR / (
        f"ingestion-latency-{datetime.now(UTC).strftime('%Y-%m-%dT%H%M%S-%fZ')}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Aggregated {report['sample_count']} ingestion samples; report={target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
