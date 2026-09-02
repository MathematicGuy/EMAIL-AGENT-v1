"""Private filesystem storage used by the single-machine SQLite runtime."""

import asyncio
import shutil
from pathlib import Path

from .supabase import StorageUnavailable


class LocalPrivateStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def upload_bytes(self, object_key: str, content: bytes) -> None:
        await asyncio.to_thread(self._upload_bytes, object_key, content)

    async def object_exists(self, object_key: str) -> bool:
        return await asyncio.to_thread(lambda: self._path(object_key).is_file())

    async def download_to(self, object_key: str, target: Path) -> None:
        await asyncio.to_thread(self._download_to, object_key, target)

    async def delete(self, object_key: str) -> None:
        await asyncio.to_thread(self._delete, object_key)

    async def create_signed_upload_url(self, object_key: str) -> str:
        del object_key
        return ""

    async def create_signed_download_url(self, object_key: str, expires_in: int) -> str:
        del object_key, expires_in
        raise StorageUnavailable("local storage does not expose filesystem paths")

    def _upload_bytes(self, object_key: str, content: bytes) -> None:
        try:
            path = self._path(object_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError as exc:
            raise StorageUnavailable("local file upload failed") from exc

    def _download_to(self, object_key: str, target: Path) -> None:
        try:
            source = self._path(object_key)
            if not source.is_file():
                raise FileNotFoundError(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        except OSError as exc:
            raise StorageUnavailable("local file download failed") from exc

    def _delete(self, object_key: str) -> None:
        try:
            self._path(object_key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageUnavailable("local file deletion failed") from exc

    def _path(self, object_key: str) -> Path:
        relative = Path(object_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise StorageUnavailable("invalid local storage key")
        return self._root / relative
