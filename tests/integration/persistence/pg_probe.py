"""One cached PostgreSQL reachability probe for the whole persistence suite.

Nine modules each defined their own ``_server_available()`` and each paid a 3 s
``connect_timeout`` at import time. On a machine without Postgres that is ~19 s
per run spent proving the same negative nine times -- longer than the entire
unit suite. The verdict is now cached per URL, so a distinct connection string
is attempted at most once per process.

Each module keeps its own ``DATABASE_URL`` and its own skip message: the two
that default to ``""`` mean "not configured" and must not start guessing at the
dev container.
"""

from __future__ import annotations

from functools import cache

CONNECT_TIMEOUT_SECONDS = 3


@cache
def server_available(database_url: str) -> bool:
    """True when ``database_url`` accepts a connection. Cached per URL."""
    if not database_url:
        return False
    try:
        import psycopg
    except ImportError:  # psycopg is an environment-dependent extra
        return False
    try:
        with psycopg.connect(database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS):
            return True
    except psycopg.Error:
        return False
