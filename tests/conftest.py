"""Suite-wide guards: hostile ambient environment and the marker taxonomy.

Every guard here exists to make a run's outcome depend on the code under test
rather than on whatever the developer's shell, ``.env``, or network happens to
be doing that afternoon.
"""

import logging
import os
import socket
import ssl
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

#: This checkout's ``src``, not whatever the venv's editable install points at.
#: Worktrees share ``C:\\WORK\\EMAIL-AGENT-v1\\.venv``, whose ``.pth`` names the
#: main tree. Without this pin, ``import cowork_agent`` on a feature worktree
#: resolves to the wrong tree and new modules raise ``ModuleNotFoundError``.
CHECKOUT_SRC = Path(__file__).resolve().parents[1] / "src"

_SERIAL_GROUP = pytest.mark.xdist_group("serial")


def prefer_checkout_src(path_entries: list[str] | None = None) -> list[str]:
    """Put this checkout's ``src`` first so it wins over the editable install."""
    entries = sys.path if path_entries is None else path_entries
    src = str(CHECKOUT_SRC)
    while src in entries:
        entries.remove(src)
    entries.insert(0, src)
    return entries


prefer_checkout_src()


def pytest_configure(config: pytest.Config) -> None:
    # Markers live in pyproject.toml [tool.pytest.ini_options]; `--strict-markers`
    # turns a typo into a collection error instead of a silently unselectable test.
    _neutralize_broken_cert_bundle(config)
    _pin_offline_rag_provider(config)
    _isolate_control_plane_target(config)
    _apply_default_xdist(config)


def _apply_default_xdist(config: pytest.Config) -> None:
    """Fan out to 4 workers when xdist is loaded and the user did not pick ``-n``.

    Worker flags live here, not in ``addopts``, so ``-p no:xdist`` is not a
    usage error. ``tests/xdist_plugin.py`` injects the same defaults earlier
    when that plugin is on the command line; this is the fallback.
    """
    if not config.pluginmanager.hasplugin("xdist"):
        return
    num = getattr(config.option, "numprocesses", None)
    if num is None:
        config.option.numprocesses = 4
    if getattr(config.option, "dist", "no") == "no" and config.option.numprocesses:
        config.option.dist = "loadgroup"


def apply_serial_xdist_group(items: list[pytest.Item]) -> None:
    """Pin ``serial`` tests to one worker so they need not force ``-n 0``."""
    for item in items:
        if item.get_closest_marker("serial") and not item.get_closest_marker("xdist_group"):
            item.add_marker(_SERIAL_GROUP)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    apply_serial_xdist_group(items)


def _isolate_control_plane_target(config: pytest.Config) -> None:
    """Do not inherit the developer's POSTGRES_MODE / DATABASE_URL.

    ``POSTGRES_MODE=local`` in ``.env`` would bind ``create_app()`` to the
    real ``cowork`` database. API tests then send email-shaped user ids
    into UUID columns. Persistence tests keep their own ``PG_TEST_URL``.
    A test that needs a URL sets it via ``monkeypatch`` or a tmp ``.env``.
    """
    os.environ["POSTGRES_MODE"] = "off"
    os.environ.pop("DATABASE_URL", None)


def _pin_offline_rag_provider(config: pytest.Config) -> None:
    """Default ``RAG_STORE_PROVIDER`` to ``none`` so app boot stays offline.

    ``build_semantic_memory`` defaults to ``turbovec``, which loads the whole
    committed corpus and then calls the real Jina embedding API to build an
    index. ``create_app()`` runs it during lifespan startup, so every test that
    boots the API paid a live HTTP round trip on credentials read out of the
    developer's ``.env``.

    It "passed" only because the key was 403ing and the except-branch degraded
    to ``NullSemanticMemory`` -- every assertion in the API tier ran against the
    degraded object by accident. What the suite tested was decided by ambient
    state, not by code: a dead key gave ``NullSemanticMemory`` (~24 s), a
    working key gave a real Jina-embedded index rebuilt on every boot
    (~115-155 s cold), and a leftover ``.data/turbovec_index.tvim`` gave a
    third thing again. Pinning the disabled provider removes the variable and
    puts the cold-cache suite at ~22 s. See tests/README.md section 7.

    Tests that mean to exercise the turbovec branch set the variable themselves
    via ``monkeypatch`` (see unit/integrations/test_bootstrap.py, which also
    stubs ``load_corpus`` and ``JinaEmbeddingAdapter`` so it stays offline).
    """
    if os.environ.get("RAG_STORE_PROVIDER"):
        return
    os.environ["RAG_STORE_PROVIDER"] = "none"
    _warn(
        config,
        "RAG_STORE_PROVIDER was unset; pinned to 'none' so create_app() cannot "
        "reach the live embedding API. Export it explicitly to override.",
    )


def _neutralize_broken_cert_bundle(config: pytest.Config) -> None:
    """Drop ``SSL_CERT_FILE``/``REQUESTS_CA_BUNDLE`` when they point nowhere.

    A stale Anaconda export (``E:\\CODE\\Anaconda/ssl/cacert.pem``) survives long
    after the interpreter it belonged to is gone. Python's ``ssl`` module raises
    ``FileNotFoundError`` from ``load_verify_locations`` at *context creation*,
    so the failure surfaces during FastAPI lifespan startup as an unrelated
    stack trace -- it cost this repo 23 phantom failures across 6 files. Tests
    talk to fakes, so an unset bundle is strictly safer than a dangling one.
    """
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        value = os.environ.get(name)
        if value and not Path(value).is_file():
            del os.environ[name]
            _warn(
                config,
                f"{name} pointed at a missing file ({value!r}); unset for this run. "
                "Fix your shell profile to stop it coming back.",
            )
    # Re-derive the default context paths now that the env is clean.
    ssl.create_default_context()


def _warn(config: pytest.Config, message: str) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"[tests/conftest] {message}", yellow=True)


_DESELECTED: list[str] = []


def pytest_deselected(items: list[pytest.Item]) -> None:
    # Only the opt-in tiers are worth reporting. Under an explicit positive
    # selection (`-m live`) the deselected set is the entire rest of the suite,
    # and listing 90 files would bury the very signal this exists to give.
    _DESELECTED.extend(
        item.nodeid
        for item in items
        if item.get_closest_marker("live") or item.get_closest_marker("slow")
    )


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """Restate what the marker filter left out, so it can't rot unnoticed.

    ``addopts`` carries ``-m "not live"``: the right default, since those tests
    need a real server and credentials. But a green "N passed" is misleading if
    nobody notices that M tests were never *considered*, and pytest's own
    one-line deselect count scrolls off the top of a long run.

    Under xdist the exact node ids are unavailable here -- ``pytest_deselected``
    fires on the workers, whose output the controller does not relay -- so the
    count degrades gracefully to naming the filter instead of the tests.
    """
    markexpr = str(config.option.markexpr or "")
    # "not ..." is the default exclusion; a positive selection deselects nothing
    # the caller did not already know about.
    if not _DESELECTED and "not " not in markexpr:
        return
    # ASCII only: the Windows console this repo is developed on mangles em dashes.
    terminalreporter.write_sep("=", "DESELECTED - NOT VERIFIED BY THIS RUN", yellow=True)
    if _DESELECTED:
        files = sorted({nodeid.split("::", 1)[0] for nodeid in _DESELECTED})
        terminalreporter.write_line(
            f"{len(_DESELECTED)} test(s) across {len(files)} file(s) never ran:", yellow=True
        )
        for path in files:
            terminalreporter.write_line(f"  - {path}", yellow=True)
    else:
        terminalreporter.write_line(
            f'Marker filter -m "{markexpr}" was active; matching tests never ran.', yellow=True
        )
    terminalreporter.write_line(
        'Opt in with:  uv run pytest -m "live or slow"   (see tests/README.md)', yellow=True
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    """Silence the atexit log storm that follows a green run.

    Langfuse registers an ``atexit`` handler that flushes its queues and logs
    each step at DEBUG. That fires *after* pytest has closed the streams it
    captured, so every run ended with a wall of::

        --- Logging error ---
        ValueError: I/O operation on closed file.

    Nothing is wrong, which is precisely the problem: noise printed under a
    passing run is how a suite teaches people to stop reading its output.
    Detach the loggers while the streams are still valid, then stop logging
    from reporting handler failures at all for the dying interpreter.
    """
    del config
    for name in ("langfuse", "langfuse._client", "httpx", "httpcore"):
        noisy = logging.getLogger(name)
        noisy.handlers.clear()
        noisy.propagate = False
        noisy.disabled = True
    logging.raiseExceptions = False


# ---------------------------------------------------------------------------
# Outbound network guard
# ---------------------------------------------------------------------------

#: Loopback stays open: the Postgres tier talks to 127.0.0.1:5432 and the live
#: E2E module drives a real ``mail-todo-api`` subprocess on localhost:8000.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", ""})

#: Connection strings an operator sets on purpose. A database someone pointed
#: the suite at is a declared dependency, not an accidental internet call, and
#: ``pg_probe.server_available`` only catches ``psycopg.Error`` -- a guard
#: RuntimeError would turn its "no server, skip" path into a hard error.
_DB_URL_VARS = ("PG_TEST_URL", "DATABASE_URL")

_real_connect = socket.socket.connect


def _configured_db_hosts() -> frozenset[str]:
    hosts: set[str] = set()
    for var in _DB_URL_VARS:
        url = os.environ.get(var, "").strip()
        if not url:
            continue
        host = urlsplit(url).hostname
        if host:
            hosts.add(host)
    return frozenset(hosts)


def _host_of(address: object) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None


@pytest.fixture(autouse=True)
def _no_external_network(request: pytest.FixtureRequest) -> Iterator[None]:
    """Turn any non-loopback connect into a loud failure.

    The RAG bootstrap reached the live Jina API from inside ``create_app()``
    unnoticed, because the failure path was a silent degrade. A test that
    quietly depends on the internet is neither deterministic nor reproducible
    in CI, so make the dependency impossible to reintroduce without seeing
    this error.

    ``live``-marked tests opt out by definition -- real Gmail OAuth and real
    Gemini calls are the thing they exist to exercise.
    """
    if request.node.get_closest_marker("live"):
        yield
        return

    allowed = _LOOPBACK | _configured_db_hosts()

    def guarded_connect(self: socket.socket, address: object) -> object:
        host = _host_of(address)
        if host is not None and host not in allowed:
            raise RuntimeError(
                f"Blocked outbound connection to {host!r} from "
                f"{request.node.nodeid}. Unit and integration tests must not "
                "touch the network -- stub the adapter (see "
                "unit/integrations/test_bootstrap.py) or mark the test `live`."
            )
        return _real_connect(self, address)  # type: ignore[arg-type]

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = _real_connect  # type: ignore[method-assign]
