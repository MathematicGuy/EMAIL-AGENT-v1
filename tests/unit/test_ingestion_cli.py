from __future__ import annotations

from pathlib import Path

from cowork_agent import ingestion_cli
from cowork_agent.integrations.knowledge_ingestion.models import IngestionOutcome


def test_cli_rejects_nested_source_and_output(tmp_path: Path, monkeypatch, capsys) -> None:
    """Accepting nested paths lets the CLI rediscover its own Markdown output."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    source = tmp_path / "source"
    source.mkdir()

    exit_code = ingestion_cli.main(["--source", str(source), "--output", str(source / "out")])

    assert exit_code == 2
    assert "source_output_nested" in capsys.readouterr().err


def test_cli_returns_two_for_missing_required_arguments() -> None:
    """Letting argparse escape as SystemExit makes the reusable CLI entry point unsafe."""
    assert ingestion_cli.main([]) == 2


def test_cli_returns_one_when_the_service_reports_an_input_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Returning success for a failed document would hide a missing corpus entry."""
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("KNOWLEDGE_INGEST_OCR_ENABLED", "false")
    monkeypatch.setattr(ingestion_cli, "KnowledgeIngestionService", _FailingService)

    exit_code = ingestion_cli.main(["--source", str(source), "--output", str(tmp_path / "out")])

    assert exit_code == 1
    assert "mistral_not_configured" in capsys.readouterr().out


class _FailingService:
    def __init__(self, *args: object) -> None:
        pass

    def ingest(self, *args: object, **kwargs: object) -> tuple[IngestionOutcome, ...]:
        return (IngestionOutcome("scan.pdf", "failed", reason_code="mistral_not_configured"),)
