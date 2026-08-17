"""The suite's own offline guarantee, asserted rather than assumed.

``tests/conftest.py::_no_external_network`` is what stops a silent live-API
dependency from creeping back into app boot (it did once, in the RAG
bootstrap, and cost ~10 s per run while making every API test assert against
an accidentally-degraded object). A guard nobody tests is a guard that rots,
so these three cases pin its contract: external blocked, loopback allowed.

Addresses are IP literals on purpose -- a hostname would need a real DNS
query, which is exactly the environment dependency this file exists to
forbid. The guard hooks ``connect``, not ``getaddrinfo``, so blocking is
enforced at the point data could actually be exchanged.
"""

import socket
import urllib.error
import urllib.request

import pytest

# TEST-NET-1 (RFC 5737): reserved for documentation, routable to nobody.
_EXTERNAL = "192.0.2.1"


def test_raw_socket_to_an_external_host_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="Blocked outbound connection"):
        socket.create_connection((_EXTERNAL, 80), timeout=5)


def test_http_client_to_an_external_host_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="Blocked outbound connection"):
        urllib.request.urlopen(f"http://{_EXTERNAL}/", timeout=5)


def test_loopback_stays_reachable_for_the_postgres_and_live_tiers() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=5)
        client.close()
    finally:
        server.close()
