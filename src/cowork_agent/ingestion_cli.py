"""Command-line entry point for local administrator knowledge ingestion."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from cowork_agent.config import KnowledgeIngestionSettings
from cowork_agent.integrations.knowledge_ingestion.service import KnowledgeIngestionService


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    try:
        settings = KnowledgeIngestionSettings.from_env()
        outcomes = KnowledgeIngestionService(settings).ingest(
            arguments.source, arguments.output, arguments.force, dry_run=arguments.dry_run
        )
    except (ValueError, OSError) as error:
        print(str(error), file=__import__("sys").stderr)
        return 2
    for outcome in outcomes:
        suffix = f" ({outcome.reason_code})" if outcome.reason_code else ""
        print(f"{outcome.status}: {outcome.source}{suffix}")
    return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest local PDF, DOCX, TXT, and MD files into Markdown."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
