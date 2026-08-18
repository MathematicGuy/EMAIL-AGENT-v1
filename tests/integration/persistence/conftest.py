"""Shared xdist grouping for the Postgres control-plane integration suite.

Every module here resets the same ``cowork_mail_todo`` database with
``DROP SCHEMA public CASCADE``. Under ``-n 4 --dist loadfile`` that
deadlocks: one worker waits for ACCESS EXCLUSIVE while another holds a
pool checkout. ``xdist_group`` + ``--dist loadgroup`` keeps the whole
package on one worker. Other tests stay parallel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PACKAGE = Path(__file__).resolve().parent
_GROUP = pytest.mark.xdist_group("pg-control-plane")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Pin only this directory. A bare pytestmark here does not attach to modules."""
    for item in items:
        try:
            path = Path(item.path).resolve()
        except AttributeError:
            path = Path(str(item.fspath)).resolve()
        if path.is_relative_to(_PACKAGE):
            item.add_marker(_GROUP)
