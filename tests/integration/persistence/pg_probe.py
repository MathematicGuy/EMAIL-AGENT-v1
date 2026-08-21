"""One cached PostgreSQL reachability probe for the whole persistence suite.

Nine modules each defined their own ``_server_available()`` and each paid a 3 s
``connect_timeout`` at import time. On a machine without Postgres that is ~19 s
per run spent proving the same negative nine times -- longer than the entire
unit suite. The verdict is now cached per URL, so a distinct connection string
is attempted at most once per process.

That cache is per *process*, and xdist gives every worker its own. Collection
imports every module on all four workers, so the probe is on the critical path
of the whole run, four times over -- and on this machine nothing answers
127.0.0.1:5432 at all (Docker's proxy blackholes the port instead of refusing),
so each probe burned the full ``connect_timeout``. Hence the TCP pre-flight
below: a dead port is now settled in ~0.3 s instead of ~3.2 s.

Each module keeps its own ``DATABASE_URL`` and its own skip message. They all
default to ``""``, which means "not configured" and must not start guessing at
the dev container: several of these modules run ``DROP SCHEMA public CASCADE``
in an autouse fixture, so a default that happened to reach the dev container
would wipe a developer's database on a bare ``pytest -m extended``. There is
deliberately no ``DEFAULT_PG_TEST_URL`` here -- ``PG_TEST_URL`` must name a
throwaway database explicitly.
"""

from __future__ import annotations

import socket
from functools import cache
from urllib.parse import urlsplit

CONNECT_TIMEOUT_SECONDS = 3

#: Ceiling on the TCP handshake alone, not on authentication or startup, so a
#: timeout means nothing is listening rather than "the server was busy".
#: Loopback accepts in well under a millisecond, so 0.25 s is ~250x headroom;
#: a remote host has real RTT to pay, so it keeps a full second. Anything that
#: *does* answer falls through to the real connect, which keeps its 3 s.
LOOPBACK_PREFLIGHT_TIMEOUT_SECONDS = 0.25
REMOTE_PREFLIGHT_TIMEOUT_SECONDS = 1.0

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def preflight_timeout(host: str) -> float:
    return (
        LOOPBACK_PREFLIGHT_TIMEOUT_SECONDS
        if host in _LOOPBACK_HOSTS
        else REMOTE_PREFLIGHT_TIMEOUT_SECONDS
    )


def _tcp_port_answers(database_url: str) -> bool:
    """True unless the TCP port is provably unreachable.

    Deliberately optimistic: an unparseable URL or an unexpected socket error
    returns True so the real ``psycopg.connect`` gets to make the call. This
    exists to make a *negative* cheap, never to turn a working server into a
    silent skip.
    """
    parts = urlsplit(database_url)
    host, port = parts.hostname, parts.port or 5432
    if not host:
        return True
    try:
        with socket.create_connection((host, port), preflight_timeout(host)):
            return True
    except (TimeoutError, ConnectionRefusedError, socket.gaierror):
        return False
    except OSError:
        return True


@cache
def server_available(database_url: str) -> bool:
    """True when ``database_url`` accepts a connection. Cached per URL."""
    if not database_url:
        return False
    try:
        import psycopg
    except ImportError:  # psycopg is an environment-dependent extra
        return False
    if not _tcp_port_answers(database_url):
        return False
    try:
        with psycopg.connect(database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS):
            return True
    except psycopg.Error:
        return False
