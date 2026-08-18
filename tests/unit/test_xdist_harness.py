"""Harness contracts that keep the default 4-worker suite usable.

Agents keep hitting two traps and then "fix" them with ``-n 0``, which
throws away the machine's cores:

- ``-p no:xdist`` plus ``addopts = -n 4 --dist loadgroup`` is a usage error
  (xdist is gone, so ``-n`` / ``--dist`` are unknown flags).
- The venv editable install points at the main worktree, so a checkout that
  is not that tree must still import *its own* ``src``.

These tests pin the escape hatches so the default stays ``-n 4``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.conftest import CHECKOUT_SRC, apply_serial_xdist_group, prefer_checkout_src
from tests.xdist_plugin import reconcile_xdist_args, xdist_plugin_disabled

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_disabling_the_xdist_plugin_is_detected() -> None:
    assert xdist_plugin_disabled(["-p", "no:xdist", "-q"]) is True
    assert xdist_plugin_disabled(["-pno:xdist"]) is True
    assert xdist_plugin_disabled(["-n", "4", "--dist", "loadgroup"]) is False


def test_reconcile_strips_worker_flags_only_when_xdist_is_disabled() -> None:
    disabled = ["-p", "no:xdist", "-n", "4", "--dist", "loadgroup", "-q"]
    reconcile_xdist_args(disabled)
    assert disabled == ["-p", "no:xdist", "-q"]

    enabled = ["-n", "4", "--dist", "loadgroup", "-q"]
    reconcile_xdist_args(enabled)
    assert enabled == ["-n", "4", "--dist", "loadgroup", "-q"]


def test_reconcile_injects_four_workers_when_the_user_did_not_pick_n() -> None:
    args = ["-q", "tests/unit/test_prompting.py"]
    reconcile_xdist_args(args)
    assert args[:4] == ["-n", "4", "--dist", "loadgroup"]
    assert args[4:] == ["-q", "tests/unit/test_prompting.py"]


def test_reconcile_strips_glued_and_equals_forms() -> None:
    args = ["-p", "no:xdist", "-n4", "--dist=loadgroup", "--numprocesses=4"]
    reconcile_xdist_args(args)
    assert args == ["-p", "no:xdist"]


def test_checkout_src_is_moved_to_the_front_of_sys_path() -> None:
    entries = [str(CHECKOUT_SRC), "other", str(CHECKOUT_SRC)]
    prefer_checkout_src(entries)
    assert entries[0] == str(CHECKOUT_SRC)
    assert entries.count(str(CHECKOUT_SRC)) == 1


class _FakeItem:
    def __init__(self, *names: str) -> None:
        self._markers = {name: object() for name in names}
        self.added: list[str] = []

    def get_closest_marker(self, name: str) -> object | None:
        return self._markers.get(name)

    def add_marker(self, mark: object) -> None:
        name = getattr(mark, "name", "xdist_group")
        self._markers[name] = mark
        self.added.append(name)


def test_serial_tests_are_grouped_instead_of_forcing_n0() -> None:
    serial = _FakeItem("serial")
    already_grouped = _FakeItem("serial", "xdist_group")
    parallel = _FakeItem()
    apply_serial_xdist_group([serial, already_grouped, parallel])  # type: ignore[arg-type]
    assert serial.added == ["xdist_group"]
    assert already_grouped.added == []
    assert parallel.added == []


def test_cowork_agent_is_imported_from_this_checkout() -> None:
    import cowork_agent

    expected = REPO_ROOT / "src" / "cowork_agent"
    assert Path(cowork_agent.__file__).resolve().parent == expected.resolve()


def test_disabling_the_xdist_plugin_is_not_a_usage_error() -> None:
    """``-p no:xdist`` must collect, not die on leftover ``-n`` / ``--dist``.

    This is the command agents copy from other repos. If it usage-errors they
    switch the whole plan to ``-n 0`` and every later run is serial.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_prompting.py",
            "--collect-only",
            "-q",
            "-p",
            "no:xdist",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "unrecognized arguments" not in combined
