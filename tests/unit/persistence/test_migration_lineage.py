"""The migration lineage must not contain two files sharing a number.

Migrations apply in filename order, so a duplicate number means whichever file
sorts first wins and the other may silently no-op. That happened with `007`:
`007_projects_documents.sql` added `task_episodes.project_id` as `uuid` and
`007_task_episode_project_scope.sql` tried to add the same column as `text`,
guarded by `IF NOT EXISTS`. Both are recorded as applied, the column is `uuid`,
and the application code was written against the `text` intent - so every
episodic write failed on PostgreSQL while SQLite, whose column is untyped, was
unaffected.
"""

from __future__ import annotations

import collections
from pathlib import Path

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "cowork_agent"
    / "persistence"
    / "migrations"
)

#: Numbers already duplicated when this guard was added. They cannot be
#: renumbered: both are applied in existing databases and `schema_migrations` is
#: keyed by filename, so renaming one would re-apply it. The guard exists to stop
#: the lineage acquiring any *new* collision.
_KNOWN_COLLISIONS = frozenset({"005", "006", "007", "012", "014"})


def _forward_migrations() -> list[Path]:
    return sorted(
        path
        for path in _MIGRATIONS_DIR.glob("*.sql")
        if not path.name.endswith(".down.sql")
    )


def test_the_migrations_directory_is_found() -> None:
    # A wrong path would make every assertion below vacuously true.
    assert _forward_migrations(), f"no migrations found under {_MIGRATIONS_DIR}"


def test_no_new_duplicate_migration_numbers() -> None:
    by_number: dict[str, list[str]] = collections.defaultdict(list)
    for path in _forward_migrations():
        by_number[path.name.split("_", 1)[0]].append(path.name)

    duplicates = {
        number: names for number, names in by_number.items() if len(names) > 1
    }
    unexpected = {
        number: names
        for number, names in duplicates.items()
        if number not in _KNOWN_COLLISIONS
    }
    assert not unexpected, (
        "migrations share a number, so filename order decides which wins and the "
        f"other may silently no-op: {unexpected}"
    )


def test_known_collisions_are_still_collisions() -> None:
    # If a collision is ever resolved, this list must shrink with it. Otherwise
    # the allowlist quietly grants an exemption nothing needs any more.
    by_number: dict[str, list[str]] = collections.defaultdict(list)
    for path in _forward_migrations():
        by_number[path.name.split("_", 1)[0]].append(path.name)

    stale = {
        number for number in _KNOWN_COLLISIONS if len(by_number.get(number, [])) < 2
    }
    assert not stale, f"these numbers no longer collide; remove them from the allowlist: {stale}"
