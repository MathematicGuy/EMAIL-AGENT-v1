import asyncio
from pathlib import Path

import httpx
import pytest

from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage

pytestmark = pytest.mark.extended


def test_signed_urls_use_server_secret_and_canonical_private_object_path() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"url": "/object/upload/sign/project-documents/key?token=x"}
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = SupabasePrivateStorage(
            "https://project.supabase.co", "server-secret", "project-documents", client
        )
        url = await storage.create_signed_upload_url(
            "workspace/a/user/b/project/c/document/d/source"
        )
        await client.aclose()

        assert url == "https://project.supabase.co/storage/v1/object/upload/sign/project-documents/key?token=x"

    asyncio.run(scenario())
    request = calls[0]
    assert request.headers["apikey"] == "server-secret"
    assert request.headers["authorization"] == "Bearer server-secret"
    expected_path = (
        "/storage/v1/object/upload/sign/project-documents/"
        "workspace/a/user/b/project/c/document/d/source"
    )
    assert request.url.path.endswith(expected_path)
    assert b"server-secret" not in request.content


def test_download_to_keeps_private_source_out_of_postgres_and_memory(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer server-secret"
        return httpx.Response(200, content=b"private source bytes")

    async def scenario() -> None:
        target = tmp_path / "source.pdf"
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        storage = SupabasePrivateStorage(
            "https://project.supabase.co", "server-secret", "project-documents", client
        )
        await storage.download_to("workspace/a/user/b/project/c/document/d/source", target)
        await client.aclose()
        assert target.read_bytes() == b"private source bytes"

    asyncio.run(scenario())
