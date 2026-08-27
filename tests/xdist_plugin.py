"""Keep default 4-worker xdist without making ``-p no:xdist`` a usage error.

``addopts`` used to carry ``-n 4 --dist loadgroup``. Disabling the plugin then
made those flags unknown and pytest exited before collecting anything. Agents
"fixed" that with ``-n 0`` and every later run was serial.

Defaults are applied from ``tests/conftest.py`` only when the plugin is loaded.
This module is the argument-level escape hatch: if ``-n`` / ``--dist`` ever
land back in ``addopts``, ``reconcile_xdist_args`` strips them when xdist is
disabled so the usage error cannot return.
"""

from __future__ import annotations


def xdist_plugin_disabled(args: list[str]) -> bool:
    """True when the command line unloads xdist (``-p no:xdist``)."""
    for index, arg in enumerate(args):
        if arg == "-p" and index + 1 < len(args) and "no:xdist" in args[index + 1]:
            return True
        if arg.startswith("-p") and arg != "-p" and "no:xdist" in arg:
            return True
    return False


def _has_numprocesses(args: list[str]) -> bool:
    return any(
        arg in {"-n", "--numprocesses"}
        or (arg.startswith("-n") and arg != "-n")
        or arg.startswith("--numprocesses=")
        for arg in args
    )


def reconcile_xdist_args(args: list[str]) -> None:
    """Drop leftover worker flags when xdist is unloaded; else apply defaults.

    Default is ``-n 4 --dist loadgroup`` so a bare ``uv run pytest`` still
    fans out. An explicit ``-n`` / ``--numprocesses`` wins. ``-p no:xdist``
    strips those flags so they cannot become a usage error.
    """
    if xdist_plugin_disabled(args):
        stripped: list[str] = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg in {"-n", "--numprocesses", "--dist"}:
                skip_next = True
                continue
            if arg.startswith("-n") and arg != "-n":
                continue
            if arg.startswith("--numprocesses=") or arg.startswith("--dist="):
                continue
            stripped.append(arg)
        args[:] = stripped
        return
    if not _has_numprocesses(args):
        args[:] = ["-n", "4", "--dist", "loadgroup", *args]


def pytest_load_initial_conftests(early_config: object, parser: object, args: list[str]) -> None:
    del early_config, parser
    reconcile_xdist_args(args)
