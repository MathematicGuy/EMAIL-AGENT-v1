from __future__ import annotations

import asyncio

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.live_env import (
    POSTGRES_DEFAULT_URL,
    probe_environment,
    run_with_selector_loop,
    unavailable_scopes,
)


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "PG_TEST_URL": "postgresql://x/y",
        "GEMINI_API_KEY": "k",
        "JINA_API_KEY": "j",
    }
    base.update(overrides)
    return base


def test_everything_present_reports_no_unavailable_scopes() -> None:
    env = probe_environment(_env(), postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://x/y"
    assert env.gemini_ready is True
    assert env.jina_ready is True
    assert unavailable_scopes(env) == ()


def test_pg_test_url_wins_over_database_url() -> None:
    env = probe_environment(
        _env(DATABASE_URL="postgresql://ignored/db"), postgres_probe=lambda url: True
    )
    assert env.postgres_url == "postgresql://x/y"


def test_database_url_is_used_when_pg_test_url_is_absent() -> None:
    environ = _env()
    del environ["PG_TEST_URL"]
    environ["DATABASE_URL"] = "postgresql://fallback/db"
    env = probe_environment(environ, postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://fallback/db"


def test_the_documented_default_is_used_when_neither_is_set() -> None:
    environ = _env()
    del environ["PG_TEST_URL"]
    seen: list[str] = []

    def probe(url: str) -> bool:
        seen.append(url)
        return True

    probe_environment(environ, postgres_probe=probe)
    assert seen == [POSTGRES_DEFAULT_URL]


def test_an_unreachable_server_makes_the_two_sql_scopes_unavailable() -> None:
    env = probe_environment(_env(), postgres_probe=lambda url: False)
    assert env.postgres_url is None
    scopes = {item.scope for item in unavailable_scopes(env)}
    assert scopes == {MemoryType.LONG_TERM, MemoryType.EPISODIC}


def test_a_missing_jina_key_makes_only_semantic_unavailable() -> None:
    environ = _env()
    del environ["JINA_API_KEY"]
    env = probe_environment(environ, postgres_probe=lambda url: True)
    unavailable = unavailable_scopes(env)
    assert [item.scope for item in unavailable] == [MemoryType.SEMANTIC]
    assert "JINA_API_KEY" in unavailable[0].reason


def test_short_term_is_never_unavailable() -> None:
    # The session buffer is in-process; nothing external can take it away.
    env = probe_environment({}, postgres_probe=lambda url: False)
    assert MemoryType.SHORT_TERM not in {item.scope for item in unavailable_scopes(env)}


def test_a_numbered_gemini_key_counts_as_ready() -> None:
    environ = _env()
    del environ["GEMINI_API_KEY"]
    environ["GEMINI_API_KEY_1"] = "k1"
    assert probe_environment(environ, postgres_probe=lambda url: True).gemini_ready is True


def test_run_with_selector_loop_returns_the_coroutine_result() -> None:
    async def work() -> str:
        await asyncio.sleep(0)
        return "done"

    assert run_with_selector_loop(work()) == "done"
