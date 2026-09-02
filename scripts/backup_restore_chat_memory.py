"""Operational backup/restore for authoritative chat-memory tables (V2-M6-B).

Index-propagation N/A evidence (FR-16): user memory (chat_profiles,
chat_summary_episodes, task_episodes) lives exclusively in PostgreSQL.
Company knowledge lives in the Turbovec snapshot plus committed Markdown;
project-document chunk text now also lives in Postgres
(``project_document_chunks``). There is no derived user-memory search
index in integrations/ or scripts/. User-memory deletion and table-level
backup/restore therefore do not touch the company Turbovec snapshot.
Project chunk rows are in the same database — restore of those tables
restores searchable project text.

NO scheduler, NO recurring trigger -- this script must be invoked explicitly.
"""

import argparse
import asyncio
import os
import selectors
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

CHAT_MEMORY_TABLES: tuple[str, ...] = (
    "chat_profiles",
    "chat_summary_episodes",
    "task_episodes",
)


def _extract_user(database_url: str) -> str:
    parsed = urlparse(database_url)
    return parsed.username or "postgres"


def _extract_dbname(database_url: str) -> str:
    parsed = urlparse(database_url)
    return parsed.path.lstrip("/")


def _build_env(database_url: str) -> dict[str, str]:
    """Return a subprocess env with PGPASSWORD set from the URL (no logging)."""
    env = os.environ.copy()
    parsed = urlparse(database_url)
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return env


def _host_and_port(database_url: str) -> tuple[str, str]:
    parsed = urlparse(database_url)
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 5432)
    return host, port


async def backup_tables(
    database_url: str,
    output_path: Path,
    *,
    docker_container: str | None = None,
) -> None:
    """Backup the three chat-memory tables to a pg_dump custom-format archive.

    Raises ``RuntimeError`` with a metadata-only message on nonzero exit.
    """
    host, port = _host_and_port(database_url)
    dbname = _extract_dbname(database_url)
    user = _extract_user(database_url)
    table_flags: list[str] = [f"--table={t}" for t in CHAT_MEMORY_TABLES]

    if docker_container:
        cmd: list[str] = [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={urlparse(database_url).password or ''}",
            docker_container,
            "pg_dump",
            "-U",
            user,
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--format=custom",
            "--no-password",
            *table_flags,
            dbname,
        ]
        env = None  # docker handles its own env
    else:
        cmd = [
            "pg_dump",
            "--host",
            host,
            "--port",
            port,
            "--username",
            user,
            "--format=custom",
            "--no-password",
            *table_flags,
            dbname,
        ]
        env = _build_env(database_url)

    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            cmd,
            capture_output=True,
            env=env,
            check=False,
        )

    result = await asyncio.to_thread(_run)

    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump exited with code {result.returncode}; tables={list(CHAT_MEMORY_TABLES)}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result.stdout)
    print(
        f"backup complete: tables={list(CHAT_MEMORY_TABLES)}"
        f" output_path={output_path} size_bytes={len(result.stdout)}"
    )


async def restore_tables(
    database_url: str,
    archive_path: Path,
    *,
    docker_container: str | None = None,
) -> None:
    """Restore chat-memory tables from a pg_dump custom-format archive.

    Uses ``--clean --if-exists`` so the restore is safe for re-application.
    Raises ``RuntimeError`` with a metadata-only message on nonzero exit.
    """
    host, port = _host_and_port(database_url)
    dbname = _extract_dbname(database_url)
    user = _extract_user(database_url)
    archive_data = archive_path.read_bytes()

    if docker_container:
        cmd: list[str] = [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={urlparse(database_url).password or ''}",
            docker_container,
            "pg_restore",
            "-U",
            user,
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--clean",
            "--if-exists",
            "--no-password",
            "--dbname",
            dbname,
        ]
        env = None
    else:
        cmd = [
            "pg_restore",
            "--host",
            host,
            "--port",
            port,
            "--username",
            user,
            "--clean",
            "--if-exists",
            "--no-password",
            "--dbname",
            dbname,
        ]
        env = _build_env(database_url)

    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            cmd,
            input=archive_data,
            capture_output=True,
            env=env,
            check=False,
        )

    result = await asyncio.to_thread(_run)

    if result.returncode != 0:
        raise RuntimeError(
            f"pg_restore exited with code {result.returncode}; tables={list(CHAT_MEMORY_TABLES)}"
        )

    print(
        f"restore complete: tables={list(CHAT_MEMORY_TABLES)}"
        f" archive_path={archive_path} exit_code={result.returncode}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backup/restore authoritative chat-memory tables.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Dump chat-memory tables.")
    backup_parser.add_argument("--database-url", required=True, help="PostgreSQL connection URL.")
    backup_parser.add_argument("--output", required=True, help="Output archive path.")
    backup_parser.add_argument(
        "--docker-container",
        default=None,
        help="Run pg_dump inside this Docker container via docker exec.",
    )

    restore_parser = subparsers.add_parser("restore", help="Restore chat-memory tables.")
    restore_parser.add_argument("--database-url", required=True, help="PostgreSQL connection URL.")
    restore_parser.add_argument("--archive", required=True, help="Archive path to restore from.")
    restore_parser.add_argument(
        "--docker-container",
        default=None,
        help="Run pg_restore inside this Docker container via docker exec.",
    )

    return parser


def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    async def _run() -> None:
        if args.command == "backup":
            await backup_tables(
                args.database_url,
                Path(args.output),
                docker_container=args.docker_container,
            )
        elif args.command == "restore":
            await restore_tables(
                args.database_url,
                Path(args.archive),
                docker_container=args.docker_container,
            )

    try:
        asyncio.run(
            _run(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
