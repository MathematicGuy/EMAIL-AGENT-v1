"""Tool-intent scorer: output location, gate math, dry-run report."""

import json
from pathlib import Path

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]


def _module():
    return load_script("evaluate_tool_intent")


def _result(
    module,
    case_id: str,
    *,
    expected_start: str | None = None,
    start_exact: bool | None = None,
    resolves_backwards: bool = False,
):
    return module.ToolIntentEvalResult(
        case_id=case_id,
        tier="core",
        decline_expected=False,
        declined=False,
        refused_by=None,
        decline_reason=None,
        ok=not resolves_backwards,
        result_text="ok",
        resolved_start="2026-08-28T02:00:00+07:00" if start_exact else "2026-08-28T09:00:00+07:00",
        resolved_end=None,
        expected_start=expected_start,
        start_exact=start_exact,
        resolves_backwards=resolves_backwards,
    )


def test_tool_intent_case_selection_and_scoring_gates() -> None:
    module = _module()
    fixture = module.load_tool_intent_cases()
    selected = module.selected_cases(fixture.cases)
    ids = {case.id for case in selected}
    assert len(selected) == 14
    assert {"tq-005", "tq-006"} <= ids
    assert not {"tq-009", "tq-015", "tq-021", "tq-025"} & ids

    # Rate math
    empty = module._rate(0, 0)
    assert empty["met"] is False and empty["rate"] is None

    # Gate resolution
    results = (
        _result(module, "a", expected_start="2026-08-28T02:00:00+07:00", start_exact=True),
        _result(module, "b", expected_start="2026-08-28T02:00:00+07:00", start_exact=False),
        _result(module, "c", expected_start=None, start_exact=None),
    )
    metrics = module.build_report(results, "fake")["metrics"]
    assert metrics["start_exact"] == {"hit": 1, "total": 2, "rate": 0.5, "met": False}


def test_tool_intent_cli_dry_run_and_privacy(tmp_path: Path) -> None:
    assert (
        _module().DEFAULT_OUTPUT_DIR
        == REPO_ROOT / "evaluations" / "CHAT" / "qa-test" / "tool-intent"
    )

    output = tmp_path / "tool-intent"
    result = run_cli("evaluate_tool_intent", "--dry-run", "--output-dir", str(output))
    assert result.returncode == 0, result.stderr

    reports = list(output.glob("tool-intent-eval-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["case_count"] == 14 and report["passed"] is True

    # Privacy: no raw user message text
    payload = reports[0].read_text(encoding="utf-8")
    assert "current_message" not in payload
