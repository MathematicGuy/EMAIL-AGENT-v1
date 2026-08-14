"""Suite-wide guards: hostile ambient environment and the marker taxonomy.

Two jobs, both about making a run's outcome depend on the code under test
rather than on whatever the developer's shell happens to export.
"""

import os
import ssl
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # Markers live in pyproject.toml [tool.pytest.ini_options]; `--strict-markers`
    # turns a typo into a collection error instead of a silently unselectable test.
    _neutralize_broken_cert_bundle(config)


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
