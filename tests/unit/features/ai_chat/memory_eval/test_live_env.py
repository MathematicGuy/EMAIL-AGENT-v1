from __future__ import annotations

import asyncio

import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.live_env import (
    ALLOW_REMOTE_ENV_VAR,
    UnsafeTargetError,
    probe_environment,
    run_with_selector_loop,
    unavailable_scopes,
)


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "PG_TEST_URL": "postgresql://127.0.0.1/y",
        "GEMINI_API_KEY": "k",
        "JINA_API_KEY": "j",
    }
    base.update(overrides)
    return base


def test_everything_present_reports_no_unavailable_scopes() -> None:
    env = probe_environment(_env(), postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://127.0.0.1/y"
    assert env.gemini_ready is True
    assert env.embeddings_ready is True
    assert unavailable_scopes(env) == ()


def test_pg_test_url_wins_over_database_url() -> None:
    env = probe_environment(
        _env(DATABASE_URL="postgresql://127.0.0.1/ignored"), postgres_probe=lambda url: True
    )
    assert env.postgres_url == "postgresql://127.0.0.1/y"


def test_database_url_is_used_when_pg_test_url_is_absent() -> None:
    environ = _env()
    del environ["PG_TEST_URL"]
    environ["DATABASE_URL"] = "postgresql://127.0.0.1/fallback"
    env = probe_environment(environ, postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://127.0.0.1/fallback"


def test_postgres_mode_local_selects_the_local_url() -> None:
    environ = _env()
    del environ["PG_TEST_URL"]
    environ["POSTGRES_MODE"] = "local"
    environ["DATABASE_URL_LOCAL"] = "postgresql://127.0.0.1/local"
    env = probe_environment(environ, postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://127.0.0.1/local"


def test_postgres_mode_off_selects_sqlite_and_dials_nothing() -> None:
    # POSTGRES_MODE=off is a deliberate choice of SQLite, not an outage. app.py
    # backs long_term and episodic with SQLiteChatRepository in exactly this
    # case, so both scopes stay evaluable and no server is dialled. Reporting
    # them unavailable here would describe a system the product is not running.
    environ = _env()
    del environ["PG_TEST_URL"]
    environ["POSTGRES_MODE"] = "off"
    dialled: list[str] = []

    def probe(url: str) -> bool:
        dialled.append(url)
        return True

    env = probe_environment(environ, postgres_probe=probe)
    assert dialled == []
    assert env.postgres_url is None
    assert env.sqlite_path is not None
    assert env.durable_memory_available is True
    assert unavailable_scopes(env) == ()


def test_a_configured_but_unreachable_server_is_an_outage_not_a_sqlite_choice() -> None:
    # A URL was set and did not answer. Silently falling back to SQLite would
    # measure a different store than the one the run was pointed at.
    env = probe_environment(_env(), postgres_probe=lambda url: False)
    assert env.postgres_url is None
    assert env.sqlite_path is None
    assert env.durable_memory_available is False


def test_an_unreachable_server_makes_the_two_durable_scopes_unavailable() -> None:
    env = probe_environment(_env(), postgres_probe=lambda url: False)
    scopes = {item.scope for item in unavailable_scopes(env)}
    assert scopes == {MemoryType.LONG_TERM, MemoryType.EPISODIC}


def test_the_embedding_key_checked_follows_the_configured_provider() -> None:
    # DOCUMENT_EMBEDDING_PROVIDER defaults to gemini, so a missing JINA_API_KEY
    # says nothing about whether the corpus can be embedded. Checking the wrong
    # key is wrong in both directions: it reports semantic unavailable on a
    # working gemini setup, and reports it available on a jina setup whose only
    # key is a gemini one.
    environ = _env()
    del environ["JINA_API_KEY"]
    env = probe_environment(environ, postgres_probe=lambda url: True)

    assert env.embeddings_ready is True
    assert unavailable_scopes(env) == ()


def test_a_missing_key_for_the_configured_provider_makes_only_semantic_unavailable() -> None:
    environ = _env(DOCUMENT_EMBEDDING_PROVIDER="jina")
    del environ["JINA_API_KEY"]
    env = probe_environment(environ, postgres_probe=lambda url: True)
    unavailable = unavailable_scopes(env)

    assert [item.scope for item in unavailable] == [MemoryType.SEMANTIC]
    assert "JINA_API_KEY" in unavailable[0].reason


def test_a_gemini_only_environment_can_still_embed_the_corpus() -> None:
    environ = _env(DOCUMENT_EMBEDDING_PROVIDER="gemini")
    del environ["JINA_API_KEY"]
    env = probe_environment(environ, postgres_probe=lambda url: True)

    assert unavailable_scopes(env) == ()


def test_gemini_embeddings_need_a_gemini_key() -> None:
    environ = _env()
    del environ["GEMINI_API_KEY"]
    del environ["JINA_API_KEY"]
    env = probe_environment(environ, postgres_probe=lambda url: True)
    unavailable = unavailable_scopes(env)

    assert [item.scope for item in unavailable] == [MemoryType.SEMANTIC]
    assert "GEMINI_API_KEY" in unavailable[0].reason


def test_short_term_is_never_unavailable() -> None:
    # The session buffer is in-process; nothing external can take it away.
    env = probe_environment({}, postgres_probe=lambda url: False)
    assert MemoryType.SHORT_TERM not in {item.scope for item in unavailable_scopes(env)}


def test_a_numbered_gemini_key_counts_as_ready() -> None:
    environ = _env()
    del environ["GEMINI_API_KEY"]
    environ["GEMINI_API_KEY_1"] = "k1"
    assert probe_environment(environ, postgres_probe=lambda url: True).gemini_ready is True


def test_run_with_selector_loop_runs_on_a_selector_loop() -> None:
    # The point of the helper, and the only part worth pinning: Windows defaults
    # to ProactorEventLoop, which psycopg's async path does not support, so every
    # database call in the live tier fails on a developer machine without this.
    # Asserting only the return value would test asyncio.run, not our choice.
    async def work() -> tuple[str, bool]:
        return "done", isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)

    result, on_selector_loop = run_with_selector_loop(work())
    assert result == "done"
    assert on_selector_loop


def test_a_remote_database_is_refused_rather_than_silently_evaluated() -> None:
    # The harness seeds memory and then deletes it. Aimed at a shared or
    # production database that is a write-and-delete against real data, so a
    # remote host must be asked for explicitly rather than inferred from
    # whatever .env happens to be in the working directory.
    environ = _env(PG_TEST_URL="postgresql://u:p@db.example.com:5432/prod")
    with pytest.raises(UnsafeTargetError, match="db.example.com"):
        probe_environment(environ, postgres_probe=lambda url: True)


def test_a_remote_database_runs_when_explicitly_allowed() -> None:
    environ = _env(PG_TEST_URL="postgresql://u:p@db.example.com:5432/throwaway")
    environ[ALLOW_REMOTE_ENV_VAR] = "1"
    env = probe_environment(environ, postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://u:p@db.example.com:5432/throwaway"


def test_localhost_needs_no_opt_in() -> None:
    for host in ("localhost", "127.0.0.1"):
        environ = _env(PG_TEST_URL=f"postgresql://u:p@{host}:5432/throwaway")
        assert probe_environment(environ, postgres_probe=lambda url: True).postgres_url


def test_postgres_mode_off_wins_over_database_url_in_live_env() -> None:
    environ = _env(POSTGRES_MODE="off", DATABASE_URL="postgresql://127.0.0.1/ignored")
    del environ["PG_TEST_URL"]
    env = probe_environment(environ, postgres_probe=lambda url: True)
    assert env.postgres_url is None
    assert env.sqlite_path is not None
    assert env.durable_memory_available is True


def test_pg_test_url_overrides_postgres_mode_off_in_live_env() -> None:
    environ = _env(
        POSTGRES_MODE="off",
        PG_TEST_URL="postgresql://127.0.0.1:5432/cowork_memeval",
    )
    env = probe_environment(environ, postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://127.0.0.1:5432/cowork_memeval"
    assert env.sqlite_path is None
    assert env.durable_memory_available is True
