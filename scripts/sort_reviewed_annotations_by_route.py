"""Split reviewed Email annotations by retrieve-first evaluation bucket."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

try:
    from scripts.email_evaluation_artifacts import (
        RUBRIC_VERSION,
        atomic_write_json,
        load_json_object,
        validate_candidate_dataset,
        validate_review_export,
    )
except ModuleNotFoundError:
    from email_evaluation_artifacts import (  # type: ignore[no-redef]
        RUBRIC_VERSION,
        atomic_write_json,
        load_json_object,
        validate_candidate_dataset,
        validate_review_export,
    )

BUCKETS = (
    "no_action",
    "retrieve_first_context_optional",
    "retrieve_first_context_required",
)
DEFAULT_INPUT = Path("evaluations/EMAIL/reviewed_annotations.json")
DEFAULT_CANDIDATES = Path("evaluations/EMAIL/gmail_candidates.json")
DEFAULT_OUTPUT_DIR = Path("evaluations/EMAIL/email-routes")
LEGACY_ROUTE_FILES = ("direct_plan.json", "retrieve_rag.json")


def sort_reviewed_annotations(
    reviewed: object,
    candidates: object,
) -> dict[str, dict[str, object]]:
    """Validate, join Gmail content, and group cases by final expected route."""

    validated = validate_review_export(reviewed, expected_count=None)
    validated_candidates = validate_candidate_dataset(candidates, expected_count=None)
    candidates_by_source_id = {
        str(candidate["source_message_id"]): candidate
        for candidate in cast(list[Mapping[str, object]], validated_candidates["cases"])
    }
    grouped: dict[str, list[dict[str, object]]] = {bucket: [] for bucket in BUCKETS}

    for case in cast(list[Mapping[str, object]], validated["cases"]):
        source_message_id = str(case["source_message_id"])
        candidate = candidates_by_source_id.get(source_message_id)
        if candidate is None:
            raise ValueError(
                f"reviewed case has no candidate match for source_message_id: {source_message_id}"
            )
        if candidate["case_id"] != case["case_id"]:
            raise ValueError(f"case_id mismatch for source_message_id: {source_message_id}")

        final = cast(Mapping[str, object], case["final"])
        if not bool(final["retrieval_expected"]):
            bucket = "no_action"
        elif bool(final["company_context_required"]):
            bucket = "retrieve_first_context_required"
        else:
            bucket = "retrieve_first_context_optional"
        enriched_case = {
            "case_id": case["case_id"],
            "source_message_id": source_message_id,
            "final": dict(final),
            "gmail_content": candidate["gmail_content"],
        }
        grouped[bucket].append(enriched_case)

    return {
        bucket: {
            "schema_version": 2,
            "rubric_version": RUBRIC_VERSION,
            "evaluation_bucket": bucket,
            "case_count": len(cases),
            "cases": cases,
        }
        for bucket, cases in grouped.items()
    }


def write_route_files(
    grouped: Mapping[str, Mapping[str, object]],
    output_dir: Path,
) -> list[Path]:
    """Atomically replace all derived route files."""

    paths = [output_dir / f"{bucket}.json" for bucket in BUCKETS]
    for bucket, path in zip(BUCKETS, paths, strict=True):
        atomic_write_json(grouped[bucket], path)
    for filename in LEGACY_ROUTE_FILES:
        (output_dir / filename).unlink(missing_ok=True)
    return paths


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sort reviewed Email annotations by retrieve-first eval bucket."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        reviewed = load_json_object(args.input)
        candidates = load_json_object(args.candidates)
        grouped = sort_reviewed_annotations(reviewed, candidates)
        paths = write_route_files(grouped, args.output_dir)
        for bucket, path in zip(BUCKETS, paths, strict=True):
            print(f"{bucket}: {grouped[bucket]['case_count']} -> {path}")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"Email route sort failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
