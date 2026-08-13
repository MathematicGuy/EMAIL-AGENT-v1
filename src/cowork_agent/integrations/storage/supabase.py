"""Private Supabase Storage adapter used only by FastAPI/worker processes."""

from pathlib import Path
from urllib.parse import quote

import httpx


class StorageUnavailable(RuntimeError):
    """A private storage operation failed without exposing provider details."""


class SupabasePrivateStorage:
    """Creates signed URLs without ever returning the server credential."""

    def __init__(
        self, url: str, secret_key: str, bucket: str, client: httpx.AsyncClient
    ) -> None:
        self._url = url.rstrip("/")
        self._secret_key = secret_key
        self._bucket = bucket
        self._client = client

    async def create_signed_upload_url(self, object_key: str) -> str:
        return await self._signed_url("object/upload/sign", object_key, {"upsert": False})

    async def create_signed_download_url(self, object_key: str, expires_in: int) -> str:
        if expires_in <= 0:
            raise ValueError("expires_in must be positive")
        return await self._signed_url("object/sign", object_key, {"expiresIn": expires_in})

    async def delete(self, object_key: str) -> None:
        try:
            response = await self._client.request(
                "DELETE",
                f"{self._url}/storage/v1/object/{self._bucket}",
                headers=self._headers(),
                json={"prefixes": [object_key]},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StorageUnavailable("private storage unavailable") from exc

    async def download_to(self, object_key: str, target: Path) -> None:
        """Stream a private source into a caller-owned secure temporary path."""
        path = quote(object_key, safe="/")
        try:
            async with self._client.stream(
                "GET",
                f"{self._url}/storage/v1/object/{self._bucket}/{path}",
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                with target.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
        except (httpx.HTTPError, OSError) as exc:
            raise StorageUnavailable("private storage unavailable") from exc

    async def _signed_url(self, operation: str, object_key: str, payload: dict[str, object]) -> str:
        path = quote(object_key, safe="/")
        try:
            response = await self._client.post(
                f"{self._url}/storage/v1/{operation}/{self._bucket}/{path}",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise StorageUnavailable("private storage unavailable") from exc
        signed_path = data.get("url") if isinstance(data, dict) else None
        if not isinstance(signed_path, str) or not signed_path:
            raise StorageUnavailable("private storage unavailable")
        if signed_path.startswith("https://"):
            return signed_path
        return f"{self._url}/storage/v1{signed_path}"

    def _headers(self) -> dict[str, str]:
        return {"apikey": self._secret_key, "authorization": f"Bearer {self._secret_key}"}
