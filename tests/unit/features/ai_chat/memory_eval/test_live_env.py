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


def test_probe_environment_postgres_resolution_and_precedence() -> None:
    # Everything present
    env = probe_environment(_env(), postgres_probe=lambda url: True)
    assert env.postgres_url == "postgresql://127.0.0.1/y"
    assert env.gemini_ready is True
    assert env.embeddings_ready is True
    assert unavailable_scopes(env) == ()

    # PG_TEST_URL wins over DATABASE_URL
    env_db = probe_environment(
        _env(DATABASE_URL="postgresql://127.0.0.1/ignored"), postgres_probe=lambda url: True
    )
    assert env_db.postgres_url == "postgresql://127.0.0.1/y"

    # DATABASE_URL used when PG_TEST_URL absent
    env_fallback = _env()
    del env_fallback["PG_TEST_URL"]
    env_fallback["DATABASE_URL"] = "postgresql://127.0.0.1/fallback"
    assert (
        probe_environment(env_fallback, postgres_probe=lambda url: True).postgres_url
        == "postgresql://127.0.0.1/fallback"
    )

    # POSTGRES_MODE=local
    env_local = _env(POSTGRES_MODE="local", DATABASE_URL_LOCAL="postgresql://127.0.0.1/local")
    del env_local["PG_TEST_URL"]
    assert (
        probe_environment(env_local, postgres_probe=lambda url: True).postgres_url
        == "postgresql://127.0.0.1/local"
    )

    # PG_TEST_URL overrides POSTGRES_MODE=off
    env_override = _env(
        POSTGRES_MODE="off", PG_TEST_URL="postgresql://127.0.0.1:5432/cowork_memeval"
    )
    assert (
        probe_environment(env_override, postgres_probe=lambda url: True).postgres_url
        == "postgresql://127.0.0.1:5432/cowork_memeval"
    )


def test_probe_environment_sqlite_mode_and_scratch_isolation() -> None:
    env1 = _env(POSTGRES_MODE="off")
    del env1["PG_TEST_URL"]
    first = probe_environment(env1)

    env2 = _env(POSTGRES_MODE="off")
    del env2["PG_TEST_URL"]
    second = probe_environment(env2)

    assert first.sqlite_path is not None and second.sqlite_path is not None
    assert first.sqlite_path != second.sqlite_path
    assert first.sqlite_path_owned is True and second.sqlite_path_owned is True
    assert first.sqlite_path.name.startswith("memeval-")
    assert unavailable_scopes(first) == ()

    # POSTGRES_MODE=off wins over DATABASE_URL
    env_off = _env(POSTGRES_MODE="off", DATABASE_URL="postgresql://127.0.0.1/ignored")
    del env_off["PG_TEST_URL"]
    probed = probe_environment(env_off, postgres_probe=lambda url: True)
    assert probed.postgres_url is None
    assert probed.sqlite_path is not None and probed.durable_memory_available is True


def test_probe_environment_outage_and_unreachable_server() -> None:
    env = probe_environment(_env(), postgres_probe=lambda url: False)
    assert env.postgres_url is None
    assert env.sqlite_path is None
    assert env.durable_memory_available is False
    scopes = {item.scope for item in unavailable_scopes(env)}
    assert scopes == {MemoryType.LONG_TERM, MemoryType.EPISODIC}
    assert MemoryType.SHORT_TERM not in scopes


def test_probe_environment_embedding_key_provider_alignment() -> None:
    # Jina key missing but default provider is gemini
    env1 = _env()
    del env1["JINA_API_KEY"]
    assert probe_environment(env1, postgres_probe=lambda url: True).embeddings_ready is True

    # Missing key for configured Jina provider
    env2 = _env(DOCUMENT_EMBEDDING_PROVIDER="jina")
    del env2["JINA_API_KEY"]
    unavail = unavailable_scopes(probe_environment(env2, postgres_probe=lambda url: True))
    assert [item.scope for item in unavail] == [MemoryType.SEMANTIC]
    assert "JINA_API_KEY" in unavail[0].reason

    # Numbered gemini key counts as ready
    env3 = _env()
    del env3["GEMINI_API_KEY"]
    env3["GEMINI_API_KEY_1"] = "k1"
    assert probe_environment(env3, postgres_probe=lambda url: True).gemini_ready is True


def test_probe_environment_remote_database_guard() -> None:
    environ = _env(PG_TEST_URL="postgresql://u:p@db.example.com:5432/prod")
    with pytest.raises(UnsafeTargetError, match="db.example.com"):
        probe_environment(environ, postgres_probe=lambda url: True)

    environ[ALLOW_REMOTE_ENV_VAR] = "1"
    assert (
        probe_environment(environ, postgres_probe=lambda url: True).postgres_url
        == "postgresql://u:p@db.example.com:5432/prod"
    )

    for host in ("localhost", "127.0.0.1"):
        assert probe_environment(
            _env(PG_TEST_URL=f"postgresql://u:p@{host}:5432/db"), postgres_probe=lambda url: True
        ).postgres_url


def test_run_with_selector_loop_runs_on_a_selector_loop() -> None:
    async def work() -> tuple[str, bool]:
        return "done", isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)

    result, on_selector_loop = run_with_selector_loop(work())
    assert result == "done"
    assert on_selector_loop
