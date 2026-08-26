"""Tool-intent scorer: output location, gate math, dry-run report.

These prove the *scorer*, offline. Whether a real model resolves "2 giờ sáng
thứ Sáu" correctly is what the live run measures; nothing here can say.
"""

import json
from pathlib import Path

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]


def _module():
    return load_script("evaluate_tool_intent")


def test_default_output_directory_stays_under_the_documented_evaluations_store() -> None:
    assert (
        _module().DEFAULT_OUTPUT_DIR
        == REPO_ROOT / "evaluations" / "CHAT" / "qa-test" / "tool-intent"
    )


def test_only_the_stories_that_reach_the_argument_filler_are_scored() -> None:
    module = _module()
    fixture = module.load_tool_intent_cases()

    selected = module.selected_cases(fixture.cases)

    ids = {case.id for case in selected}
    assert len(selected) == 14
    # The two `clarify` stories are included deliberately: the question about
    # `tq-005` is whether the model would have guessed, and the only way to know
    # is to let it try.
    assert {"tq-005", "tq-006"} <= ids
    # Nothing that routes to chat or rag -- those never reach a tool.
    assert not {"tq-009", "tq-015", "tq-021", "tq-025"} & ids


def test_a_known_good_fake_meets_every_gate(tmp_path: Path) -> None:
    output = tmp_path / "tool-intent"

    result = run_cli("evaluate_tool_intent", "--dry-run", "--output-dir", str(output))

    assert result.returncode == 0, result.stderr
    reports = list(output.glob("tool-intent-eval-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["case_count"] == 14
    assert report["passed"] is True
    assert report["model"] == "dry-run-fake"
    assert all(report["metrics"][gate]["met"] for gate in _module().GATES)


def test_the_report_does_not_echo_the_user_message(tmp_path: Path) -> None:
    """Same rule the chat-routing report follows: the measurement, not the text."""

    output = tmp_path / "tool-intent"
    run_cli("evaluate_tool_intent", "--dry-run", "--output-dir", str(output))

    payload = next(output.glob("tool-intent-eval-*.json")).read_text(encoding="utf-8")

    assert "current_message" not in payload
    assert "ready_document_titles" not in payload


def test_a_gate_with_nothing_to_measure_does_not_pass() -> None:
    """A dropped case must not read as a green run."""

    empty = _module()._rate(0, 0)

    assert empty["met"] is False
    assert empty["rate"] is None


def test_the_start_gate_counts_only_stories_that_state_a_time() -> None:
    module = _module()
    results = (
        _result(module, "a", expected_start="2026-08-28T02:00:00+07:00", start_exact=True),
        _result(module, "b", expected_start="2026-08-28T02:00:00+07:00", start_exact=False),
        _result(module, "c", expected_start=None, start_exact=None),
    )

    metrics = module.build_report(results, "fake")["metrics"]

    assert metrics["start_exact"] == {"hit": 1, "total": 2, "rate": 0.5, "met": False}


def test_a_backwards_resolution_fails_its_gate_even_when_the_guard_caught_it() -> None:
    """The guard rejecting a past date is the backstop working, not the model
    being right -- the report has to distinguish the two."""

    module = _module()
    results = (
        _result(module, "a", expected_start=None, start_exact=None, resolves_backwards=True),
    )

    metrics = module.build_report(results, "fake")["metrics"]

    assert metrics["no_backwards_resolution"]["met"] is False


def test_an_expected_decline_that_fills_arguments_instead_fails_its_gate() -> None:
    module = _module()
    results = (_result(module, "a", expected_start=None, start_exact=None, decline_expected=True),)

    metrics = module.build_report(results, "fake")["metrics"]

    assert metrics["declined_when_underdetermined"]["met"] is False


def test_the_decline_gate_counts_a_refusal_from_the_tool_as_well_as_the_filler() -> None:
    """Since the ambiguous-hour guard landed, an underdetermined request can be
    stopped in two places. The gate is about nothing reaching the calendar, so
    both count -- and `refused_by` is what says which one did the work."""

    module = _module()
    results = (
        _result(
            module,
            "a",
            expected_start=None,
            start_exact=None,
            decline_expected=True,
            declined=True,
            refused_by="tool",
        ),
    )

    metrics = module.build_report(results, "fake")["metrics"]

    assert metrics["declined_when_underdetermined"]["met"] is True


def _result(
    module,
    case_id: str,
    *,
    expected_start: str | None,
    start_exact: bool | None,
    decline_expected: bool = False,
    resolves_backwards: bool = False,
    declined: bool = False,
    refused_by: str | None = None,
):
    return module.ToolIntentEvalResult(
        case_id=case_id,
        tier="happy_path",
        decline_expected=decline_expected,
        declined=declined,
        refused_by=refused_by,
        decline_reason=None,
        ok=True,
        result_text="created",
        resolved_start=expected_start,
        resolved_end=None,
        expected_start=expected_start,
        start_exact=start_exact,
        resolves_backwards=resolves_backwards,
    )
