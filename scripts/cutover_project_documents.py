"""Irreversible legacy project-document cutover with explicit target guards."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams

from cowork_agent.config import QdrantSettings, database_url
from cowork_agent.persistence.migrate import apply_migrations

CONFIRMATION = "DESTROY_LEGACY_PROJECT_DOCUMENTS"
STORAGE_PREFIX = "workspace/"
PROJECT_COLLECTION = "project_documents"
PROJECT_VECTOR_SIZE = 3072


async def run(
    *,
    confirm: str | None,
    local_store: Path,
    expected_database_sha256: str | None,
    expected_local_store: Path | None,
    expected_storage_prefix: str | None,
    expected_qdrant_collection: str | None,
    services_stopped: bool,
    discard_canonical_rows: bool,
) -> None:
    load_dotenv(override=False)
    root = Path(__file__).resolve().parents[1]
    target = local_store.resolve()
    allowed_root = (root / ".data").resolve()
    if target != allowed_root and allowed_root not in target.parents:
        raise SystemExit(f"Refusing local target outside {allowed_root}")
    url = database_url()
    if not url:
        raise SystemExit("DATABASE_URL is required")
    qdrant = QdrantSettings.from_env()
    if qdrant.project_collection_name != PROJECT_COLLECTION:
        raise SystemExit(f"QDRANT_PROJECT_COLLECTION must equal {PROJECT_COLLECTION}")

    database_fingerprint = hashlib.sha256(url.encode()).hexdigest()
    database_parts = urlsplit(url)

    pool = AsyncConnectionPool(url, min_size=1, max_size=1, open=False)
    await pool.open(wait=True)
    client: AsyncQdrantClient | None = None
    try:
        async with pool.connection() as connection:
            counts: dict[str, int] = {}
            for table in (
                "user_chat_projects",
                "user_chat_sessions",
                "user_project_documents",
                "user_project_document_chunks",
                "projects",
                "project_documents",
                "document_ingestion_jobs",
                "chat_sessions",
            ):
                cursor = await connection.execute("SELECT to_regclass(%s)", (table,))
                if (await cursor.fetchone() or (None,))[0] is None:
                    counts[table] = 0
                else:
                    cursor = await connection.execute(f"SELECT count(*) FROM {table}")
                    row = await cursor.fetchone()
                    counts[table] = int(row[0]) if row else 0
        print(f"Legacy rows: {counts}")
        print(
            "Database target: "
            f"{database_parts.hostname or '<unknown>'}/{database_parts.path.lstrip('/')} "
            f"sha256={database_fingerprint}"
        )
        print(f"Local encrypted store: {target}")
        print(f"Supabase Storage prefix: {STORAGE_PREFIX}")
        print(f"Qdrant collection: {qdrant.project_collection_name}")
        if confirm != CONFIRMATION:
            print(f"Dry run only. Re-run with --confirm {CONFIRMATION}")
            return
        if not services_stopped:
            raise SystemExit("Refusing cutover without --services-stopped")
        if expected_database_sha256 != database_fingerprint:
            raise SystemExit("DATABASE_URL fingerprint does not match --expected-database-sha256")
        if expected_local_store is None or expected_local_store.resolve() != target:
            raise SystemExit("Local path does not match --expected-local-store")
        if expected_storage_prefix != STORAGE_PREFIX:
            raise SystemExit("Storage prefix does not match --expected-storage-prefix")
        if expected_qdrant_collection != qdrant.project_collection_name:
            raise SystemExit("Qdrant collection does not match --expected-qdrant-collection")
        canonical_rows = sum(
            counts[name]
            for name in (
                "projects",
                "project_documents",
                "document_ingestion_jobs",
                "chat_sessions",
            )
        )
        if canonical_rows and not discard_canonical_rows:
            raise SystemExit(
                "Canonical rows exist; pass --discard-canonical-rows after verifying they "
                "contain nothing to retain"
            )
        if qdrant.enabled:
            client = AsyncQdrantClient(url=qdrant.url, api_key=qdrant.api_key or None)
            if await client.collection_exists(qdrant.project_collection_name):
                await client.delete_collection(qdrant.project_collection_name)
        if target.exists():
            shutil.rmtree(target)
        applied = await apply_migrations(pool)
        if client is not None:
            await client.create_collection(
                collection_name=qdrant.project_collection_name,
                vectors_config=VectorParams(
                    size=PROJECT_VECTOR_SIZE, distance=Distance.COSINE
                ),
            )
        print(f"Cutover complete; applied migrations: {applied}")
    finally:
        if client is not None:
            await client.close()
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm")
    parser.add_argument(
        "--local-store", type=Path, default=Path(".data/project_documents")
    )
    parser.add_argument("--expected-database-sha256")
    parser.add_argument("--expected-local-store", type=Path)
    parser.add_argument("--expected-storage-prefix")
    parser.add_argument("--expected-qdrant-collection")
    parser.add_argument("--services-stopped", action="store_true")
    parser.add_argument("--discard-canonical-rows", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(
        run(
            confirm=arguments.confirm,
            local_store=arguments.local_store,
            expected_database_sha256=arguments.expected_database_sha256,
            expected_local_store=arguments.expected_local_store,
            expected_storage_prefix=arguments.expected_storage_prefix,
            expected_qdrant_collection=arguments.expected_qdrant_collection,
            services_stopped=arguments.services_stopped,
            discard_canonical_rows=arguments.discard_canonical_rows,
        )
    )


if __name__ == "__main__":
    main()
