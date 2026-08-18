"""Resolving the live tier's external dependencies (SPEC §6.1).

Three things can be missing independently: a PostgreSQL server, a Gemini key,
a Jina key. Each absence disables a specific set of scopes and nothing else.

A harness that dies on the first missing dependency tells you nothing about the
other scopes, so every absence becomes a typed finding the report can carry.
Gemini is the one exception and it is handled by the caller: with no model
there is no reply to score, so there is no run at all.
"""

from __future__ import annotations

import asyncio
import selectors
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from cowork_agent.domain.chat_contracts import MemoryType

#: Matches tests/integration/persistence/*, so one running dev container serves both.
POSTGRES_DEFAULT_URL = "postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_mail_todo"

_CONNECT_TIMEOUT_SECONDS = 3

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ScopeAvailability:
    scope: MemoryType
    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class LiveEnvironment:
    postgres_url: str | None
    gemini_ready: bool
    jina_ready: bool


def default_postgres_probe(url: str) -> bool:
    """True when `url` accepts a connection. Mirrors tests/integration/persistence/pg_probe."""

    if not url:
        return False
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(url, connect_timeout=_CONNECT_TIMEOUT_SECONDS):
            return True
    except psycopg.Error:
        return False


def _gemini_ready(environ: Mapping[str, str]) -> bool:
    # GeminiSettings.from_env accepts GEMINI_API_KEY or numbered GEMINI_API_KEY_<n>.
    if environ.get("GEMINI_API_KEY"):
        return True
    return any(name.startswith("GEMINI_API_KEY_") and value for name, value in environ.items())


def probe_environment(
    environ: Mapping[str, str],
    *,
    postgres_probe: Callable[[str], bool] = default_postgres_probe,
) -> LiveEnvironment:
    """Resolve which external dependencies are actually usable right now."""

    url = environ.get("PG_TEST_URL") or environ.get("DATABASE_URL") or POSTGRES_DEFAULT_URL
    reachable = postgres_probe(url)
    return LiveEnvironment(
        postgres_url=url if reachable else None,
        gemini_ready=_gemini_ready(environ),
        jina_ready=bool(environ.get("JINA_API_KEY")),
    )


def unavailable_scopes(env: LiveEnvironment) -> tuple[ScopeAvailability, ...]:
    """Which scopes cannot be evaluated, and why, in report-ready form.

    short_term is never listed: the session buffer is in-process, so no external
    outage can remove it.
    """

    findings: list[ScopeAvailability] = []
    if env.postgres_url is None:
        reason = "no PostgreSQL server (set PG_TEST_URL or start cowork-pg)"
        findings.append(ScopeAvailability(MemoryType.LONG_TERM, False, reason))
        findings.append(ScopeAvailability(MemoryType.EPISODIC, False, reason))
    if not env.jina_ready:
        findings.append(
            ScopeAvailability(
                MemoryType.SEMANTIC, False, "no JINA_API_KEY; corpus cannot be embedded"
            )
        )
    return tuple(findings)


def run_with_selector_loop(coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` on a selector loop.

    Windows defaults to ProactorEventLoop, which psycopg's async path does not
    support. The persistence suite does exactly this; the live tier must too or
    every database call fails on a developer machine.
    """

    return asyncio.run(
        coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    )
