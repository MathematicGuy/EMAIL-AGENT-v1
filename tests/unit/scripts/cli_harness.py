"""Shared in-process loader/runner for the ``scripts/*.py`` evaluation CLIs.

Every script under test exposes ``main(argv: Sequence[str] | None) -> int`` and
writes through ``sys.stdout``/``sys.stderr``, so a real subprocess buys nothing
that this does not — while costing a fresh interpreter plus the whole
``cowork_agent`` import graph (~1-4 s) per assertion. Before this harness the
five files in ``tests/unit/scripts`` spent ~33 s of their 40 s runtime on
process spawns.

Rule for new tests: keep **one** subprocess test per script (conventionally
``test_help_runs_without_provider_keys``) as proof the entry point is
executable, and route every other CLI assertion through :func:`run_cli`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import types
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"

_LOADED: dict[str, types.ModuleType] = {}


def load_script(name: str) -> types.ModuleType:
    """Import ``scripts/<name>.py`` once per session and reuse it.

    Re-executing the module per test re-runs its import side effects for no
    benefit. Sharing is safe as long as tests mutate it only through
    ``monkeypatch``, which pytest reverts.
    """
    cached = _LOADED.get(name)
    if cached is not None:
        return cached
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: dataclasses resolve annotations via sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _LOADED[name] = module
    return module


@dataclass(frozen=True)
class CliResult:
    """What ``subprocess.CompletedProcess`` gave us, minus the interpreter."""

    returncode: int
    stdout: str
    stderr: str


def run_cli(script: str, *argv: str) -> CliResult:
    """Call ``scripts/<script>.py:main(argv)`` in-process with stdio captured.

    ``SystemExit`` is caught so argparse validation errors (exit 2) and
    ``--help`` read the same way they would from a shell.
    """
    module = load_script(script)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            returncode = module.main(list(argv))
    except SystemExit as exc:
        returncode = 0 if exc.code is None else int(exc.code)
    return CliResult(returncode, out.getvalue(), err.getvalue())
