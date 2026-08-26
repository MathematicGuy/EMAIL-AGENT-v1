"""Score the calendar tool's argument judgement against the QA stories.

Layer B of `docs/evaluations/CHAT/SPEC-calendar-tool-qa.md`. The offline suite
(`tests/unit/features/ai_chat/test_tool_intent_qa.py`) scripts the model's
answer, so it proves the router and the handler but says nothing about whether a
model would resolve "2 giờ sáng thứ Sáu" to the right instant. That is what this
measures, and it needs a real provider.

Not a pytest test: it spends money and talks to a network, both of which
`tests/README.md` §1 keeps out of the suite.

    uv run python scripts/evaluate_tool_intent.py --dry-run   # offline, free
    uv run python scripts/evaluate_tool_intent.py             # ~14 live calls

Exit code 0 means every gate in `GATES` was met.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cowork_agent.config import (
    ChatIntentSettings,
    GeminiSettings,
    MimoSettings,
    MistralSettings,
)
from cowork_agent.domain.chat_contracts import ChatRoute
from cowork_agent.features.ai_chat.tools.arguments import ToolArgumentCompletion, fill_arguments
from cowork_agent.features.ai_chat.tools.calendar import (
    CALENDAR_TOOL_NAME,
    InMemoryCalendar,
    build_calendar_tool,
)
from cowork_agent.features.ai_chat.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "evaluations" / "CHAT" / "qa-test" / "tool-intent"
FIXTURE_LOADER = REPO_ROOT / "tests" / "fixtures" / "tool_intent" / "loader.py"
TIMEZONE = "Asia/Ho_Chi_Minh"


def _load_fixture_module():
    """Load the loader by path, as `evaluate_chat_routing.py` does -- `scripts/`
    is not on the test package's import path."""

    spec = importlib.util.spec_from_file_location("tool_intent_fixture_loader", FIXTURE_LOADER)
    if spec is None or spec.loader is None:
        raise RuntimeError("tool intent fixture loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_fixture_module = _load_fixture_module()
ToolIntentCase = _fixture_module.ToolIntentCase
load_tool_intent_cases = _fixture_module.load_tool_intent_cases


@dataclass(frozen=True, slots=True)
class ToolIntentEvalResult:
    """One story's outcome, at the granularity a failure has to be read at."""

    case_id: str
    tier: str
    # True when the story's own expectation is a refusal rather than arguments.
    decline_expected: bool
    declined: bool
    decline_reason: str | None
    ok: bool
    result_text: str
    resolved_start: str | None
    resolved_end: str | None
    expected_start: str | None
    start_exact: bool | None
    resolves_backwards: bool


# The metrics that decide the exit code. A gate with nothing to measure fails
# rather than passing vacuously -- see `_rate`.
GATES = (
    "start_exact",
    "declined_when_underdetermined",
    "no_backwards_resolution",
    "schema_accepted",
)


def selected_cases(cases: Sequence[ToolIntentCase]) -> tuple[ToolIntentCase, ...]:
    """The stories that reach the argument filler, plus the two that must not.

    A `clarify` story never reaches the tool in production. It is run here
    anyway, because the question this script exists to answer about `tq-005` is
    exactly "would the model have guessed?" -- and the only way to know is to
    give it the chance and watch it decline.
    """

    return tuple(
        case for case in cases if case.expected_final_route in (ChatRoute.TOOL, ChatRoute.CLARIFY)
    )


async def evaluate_case(
    case: ToolIntentCase, complete: ToolArgumentCompletion
) -> ToolIntentEvalResult:
    now: datetime = case.context.now
    calendar = InMemoryCalendar()
    tool = build_calendar_tool(
        calendar, idempotency_key=f"eval-{case.id}", timezone=TIMEZONE, now=now
    )
    arguments = await fill_arguments(
        complete,
        tool,
        user_message=case.current_message,
        recent_turns=(),
        now=now,
    )
    decline_expected = case.expected_final_route is ChatRoute.CLARIFY
    outcome = case.expected_tool_outcome
    expected_start: datetime | None = outcome.expect_start if outcome is not None else None

    if isinstance(arguments, str):
        return ToolIntentEvalResult(
            case_id=case.id,
            tier=str(case.tier),
            decline_expected=decline_expected,
            declined=True,
            decline_reason=arguments,
            ok=False,
            result_text=arguments,
            resolved_start=None,
            resolved_end=None,
            expected_start=expected_start.isoformat() if expected_start else None,
            start_exact=None if expected_start is None else False,
            resolves_backwards=False,
        )

    result = await ToolRegistry([tool]).run(CALENDAR_TOOL_NAME, arguments)
    event = next(iter(calendar.events.values()), None)
    start = getattr(event, "start", None)
    end = getattr(event, "end", None)
    return ToolIntentEvalResult(
        case_id=case.id,
        tier=str(case.tier),
        decline_expected=decline_expected,
        declined=False,
        decline_reason=None,
        ok=result.ok,
        result_text=result.text,
        resolved_start=start.isoformat() if start is not None else None,
        resolved_end=end.isoformat() if end is not None else None,
        expected_start=expected_start.isoformat() if expected_start else None,
        start_exact=None if expected_start is None else start == expected_start,
        # The guard already rejects these; measuring it separately says whether
        # the guard is doing the work or the model is.
        resolves_backwards=_is_backwards(start, now),
    )


def _is_backwards(start: object, now: datetime) -> bool:
    if not isinstance(start, datetime):
        return False
    return start < now - timedelta(days=1)


def build_report(results: Sequence[ToolIntentEvalResult], model_id: str) -> dict[str, object]:
    scored = [result for result in results if result.start_exact is not None]
    declines = [result for result in results if result.decline_expected]
    fills = [result for result in results if not result.decline_expected]
    metrics = {
        "start_exact": _rate(sum(bool(r.start_exact) for r in scored), len(scored)),
        "declined_when_underdetermined": _rate(sum(r.declined for r in declines), len(declines)),
        "no_backwards_resolution": _rate(
            sum(not r.resolves_backwards for r in results), len(results)
        ),
        "schema_accepted": _rate(sum(r.ok for r in fills), len(fills)),
    }
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "model": model_id,
        "case_count": len(results),
        "metrics": metrics,
        "passed": all(metrics[gate]["met"] for gate in GATES),
        "per_case": [asdict(result) for result in results],
    }


def _rate(hit: int, total: int) -> dict[str, object]:
    # A gate with nothing to measure is not a pass. Reporting it as one is how a
    # dropped case turns into a green run.
    return {
        "hit": hit,
        "total": total,
        "rate": round(hit / total, 4) if total else None,
        "met": total > 0 and hit == total,
    }


def dry_run_completion(cases: Sequence[ToolIntentCase]) -> ToolArgumentCompletion:
    """A fake that answers each story the way a correct model would.

    Its point is to prove the *scorer* -- that a correct set of answers scores
    14/14 and that the metrics are wired to the right fields. It proves nothing
    about any real model.
    """

    # Answers are served in call order rather than matched against the prompt.
    # Two stories share a message on purpose (a retry and a fresh intent), and
    # `tq-024`'s delimiter payload is neutralized before it reaches the prompt,
    # so neither is findable by substring. `evaluate` is sequential, which is
    # what makes order a reliable key.
    remaining = iter(cases)

    async def complete(prompt: str, schema: Mapping[str, object]) -> Mapping[str, object]:
        del prompt, schema
        case = next(remaining, None)
        if case is None:
            return {"error": "the fake ran out of scripted answers"}
        if case.expected_final_route is ChatRoute.CLARIFY:
            return {"error": "the message does not say which hour was meant"}
        outcome = case.expected_tool_outcome
        start = outcome.expect_start or (case.context.now + timedelta(days=2)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        end = outcome.expect_end or start + timedelta(minutes=30)
        return {
            "title": case.story[:60],
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

    return complete


def build_live_completion() -> tuple[ToolArgumentCompletion, str]:
    from cowork_agent.integrations.llm.tool_arguments import (
        gemini_tool_arguments,
        mimo_tool_arguments,
        mistral_tool_arguments,
    )

    provider_name = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider_name == "gemini":
        provider = GeminiSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=provider.model)
        return gemini_tool_arguments(provider, intent), intent.model
    if provider_name == "mimo":
        mimo = MimoSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=mimo.model)
        return mimo_tool_arguments(mimo, intent), intent.model
    if provider_name == "mistral":
        mistral = MistralSettings.from_env()
        intent = ChatIntentSettings.from_env(default_model=mistral.model)
        return mistral_tool_arguments(mistral, intent), intent.model
    raise ValueError("LLM_PROVIDER must be gemini, mimo, or mistral")


async def evaluate(
    cases: Sequence[ToolIntentCase], complete: ToolArgumentCompletion
) -> tuple[ToolIntentEvalResult, ...]:
    # Sequential on purpose: a rate limit mid-run would score as a refusal and
    # quietly report the model as more cautious than it is.
    return tuple([await evaluate_case(case, complete) for case in cases])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="score a known-good fake, offline")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    cases = selected_cases(load_tool_intent_cases().cases)
    if args.dry_run:
        complete, model_id = dry_run_completion(cases), "dry-run-fake"
    else:
        complete, model_id = build_live_completion()
    results = asyncio.run(evaluate(cases, complete))
    report = build_report(results, model_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"tool-intent-eval-{report['date']}.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"Scored {len(results)} tool-intent cases against {model_id}; "
        f"passed={report['passed']}; report={target}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
