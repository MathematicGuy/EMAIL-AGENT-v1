from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from cowork_agent.features.ai_chat.memory_eval.judge import (
    GeminiRefusalJudge,
    NullRefusalJudge,
    reconcile,
)
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome, ScoreResult


class _Transport:
    def __init__(self, payload: Mapping[str, Any] | Exception) -> None:
        self._payload = payload
        self.calls = 0

    async def generate(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls += 1
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_null_judge_always_returns_none() -> None:
    assert asyncio.run(NullRefusalJudge().adjudicate("q", "a")) is None


def test_gemini_judge_reads_the_declined_flag() -> None:
    judge = GeminiRefusalJudge(_Transport({"declined": True}), api_key="k", model="m")
    assert asyncio.run(judge.adjudicate("q", "a")) is True


def test_gemini_judge_returns_none_when_the_transport_fails() -> None:
    # An unreachable judge must not be converted into either verdict.
    judge = GeminiRefusalJudge(_Transport(RuntimeError("boom")), api_key="k", model="m")
    assert asyncio.run(judge.adjudicate("q", "a")) is None


def test_gemini_judge_returns_none_on_a_malformed_payload() -> None:
    judge = GeminiRefusalJudge(_Transport({"nope": 1}), api_key="k", model="m")
    assert asyncio.run(judge.adjudicate("q", "a")) is None


def test_judge_overrules_a_false_invented() -> None:
    heuristic = ScoreResult(Outcome.INVENTED, False, "answered")
    settled = reconcile(heuristic, declined=True)
    assert settled.outcome is Outcome.PASS
    assert settled.certain is True
    assert "overruled" in settled.why


def test_judge_overrules_a_false_pass() -> None:
    heuristic = ScoreResult(Outcome.PASS, False, "declined, as it should")
    settled = reconcile(heuristic, declined=False)
    assert settled.outcome is Outcome.INVENTED
    assert settled.certain is True


def test_judge_agreement_only_settles_certainty() -> None:
    heuristic = ScoreResult(Outcome.PASS, False, "declined, as it should")
    settled = reconcile(heuristic, declined=True)
    assert settled.outcome is Outcome.PASS
    assert settled.certain is True
    assert "judge agreed" in settled.why


def test_unreachable_judge_leaves_the_heuristic_standing_and_uncertain() -> None:
    heuristic = ScoreResult(Outcome.INVENTED, False, "answered")
    settled = reconcile(heuristic, declined=None)
    assert settled == heuristic
    assert settled.certain is False
