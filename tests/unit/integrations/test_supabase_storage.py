import asyncio

import httpx

from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage


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
