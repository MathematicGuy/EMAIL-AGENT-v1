"""The TCP pre-flight in the Postgres probe must only ever make a *negative* cheap.

``pg_probe.server_available`` gates nine integration modules at import time. It
runs on every xdist worker during collection, so its cost is on the critical
path of the whole suite -- that is why the pre-flight exists. The danger it
introduces is the mirror image: a pre-flight that is too eager turns a working
server into a silent skip, and a skipped persistence tier looks exactly like a
passing one. These tests pin the asymmetry.
"""

from __future__ import annotations

import socket

import pytest

from tests.integration.persistence import pg_probe


def test_unset_url_is_unavailable_without_touching_the_network() -> None:
    assert pg_probe.server_available("") is False


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionRefusedError(), socket.gaierror()])
def test_definitive_tcp_failure_reports_unreachable(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Nothing listening, refused, or no such host: settle it without psycopg."""

    def fail(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(pg_probe.socket, "create_connection", fail)

    assert pg_probe._tcp_port_answers("postgresql://u:p@127.0.0.1:5432/db") is False


def test_unexpected_socket_error_falls_through_to_the_real_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OSError we did not anticipate must not be read as "no server"."""

    def fail(*args: object, **kwargs: object) -> object:
        raise OSError("something else entirely")

    monkeypatch.setattr(pg_probe.socket, "create_connection", fail)

    assert pg_probe._tcp_port_answers("postgresql://u:p@127.0.0.1:5432/db") is True


def test_url_without_a_host_falls_through_to_the_real_connect() -> None:
    assert pg_probe._tcp_port_answers("not-a-url") is True


def test_loopback_gets_the_short_timeout_and_remote_keeps_the_long_one() -> None:
    # A remote host has real RTT to pay; loopback does not. Collapsing these
    # would either re-cost the suite or start skipping a live remote server.
    assert pg_probe.preflight_timeout("127.0.0.1") < pg_probe.preflight_timeout(
        "db.example.supabase.co"
    )


def test_preflight_ceiling_stays_far_below_the_connect_timeout() -> None:
    # The pre-flight is an optimization, not a second timeout policy: if it ever
    # approaches CONNECT_TIMEOUT_SECONDS it has stopped saving anything and
    # started deciding availability on its own.
    assert pg_probe.REMOTE_PREFLIGHT_TIMEOUT_SECONDS < pg_probe.CONNECT_TIMEOUT_SECONDS / 2
