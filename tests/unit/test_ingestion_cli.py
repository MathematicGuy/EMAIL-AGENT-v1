from __future__ import annotations

from pathlib import Path

from cowork_agent import ingestion_cli


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

