"""Split reviewed Email annotations and private Gmail content by final route."""

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

ROUTES = ("no_action", "direct_plan", "retrieve_rag")
DEFAULT_INPUT = Path("evaluations/EMAIL/reviewed_annotations.json")
DEFAULT_CANDIDATES = Path("evaluations/EMAIL/gmail_candidates.json")
DEFAULT_OUTPUT_DIR = Path("evaluations/EMAIL/email-routes")


def sort_reviewed_annotations(
    reviewed: object,
    candidates: object,
) -> dict[str, dict[str, object]]:
    """Validate, join Gmail content, and group cases by final expected route."""

    validated = validate_review_export(reviewed, expected_count=None)
    validated_candidates = validate_candidate_dataset(candidates, expected_count=None)
    candidates_by_source_id = {
        str(candidate["source_message_id"]): candidate
        for candidate in cast(
            list[Mapping[str, object]], validated_candidates["cases"]
        )
    }
    grouped: dict[str, list[dict[str, object]]] = {route: [] for route in ROUTES}

    for case in cast(list[Mapping[str, object]], validated["cases"]):
        source_message_id = str(case["source_message_id"])
        candidate = candidates_by_source_id.get(source_message_id)
        if candidate is None:
            raise ValueError(
                "reviewed case has no candidate match for source_message_id: "
                f"{source_message_id}"
            )
        if candidate["case_id"] != case["case_id"]:
            raise ValueError(
                f"case_id mismatch for source_message_id: {source_message_id}"
            )

        final = cast(Mapping[str, object], case["final"])
        route = str(final["expected_route"])
        if route not in grouped:
            raise ValueError(f"unsupported final expected_route: {route}")
        enriched_case = {
            "case_id": case["case_id"],
            "source_message_id": source_message_id,
            "final": dict(final),
            "gmail_content": candidate["gmail_content"],
        }
        grouped[route].append(enriched_case)

    return {
        route: {
            "schema_version": 1,
            "rubric_version": RUBRIC_VERSION,
            "expected_route": route,
            "case_count": len(cases),
            "cases": cases,
        }
        for route, cases in grouped.items()
    }


def write_route_files(
    grouped: Mapping[str, Mapping[str, object]],
    output_dir: Path,
) -> list[Path]:
    """Atomically replace all derived route files."""

    paths = [output_dir / f"{route}.json" for route in ROUTES]
    for route, path in zip(ROUTES, paths, strict=True):
        atomic_write_json(grouped[route], path)
    return paths


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sort reviewed Email annotations by final expected route."
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
        for route, path in zip(ROUTES, paths, strict=True):
            print(f"{route}: {grouped[route]['case_count']} -> {path}")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"Email route sort failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
