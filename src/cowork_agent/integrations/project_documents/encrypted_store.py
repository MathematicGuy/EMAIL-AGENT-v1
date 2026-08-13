"""Fernet-encrypted, opaque-path local object store for user documents."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class EncryptedDocumentStore:
    def __init__(self, root: Path, encryption_key: str) -> None:
        if not encryption_key or encryption_key.startswith("replace-with-"):
            raise ValueError("DOCUMENT_ENCRYPTION_KEY must be configured")
        self._root = root.resolve()
        self._cipher = Fernet(encryption_key.encode("ascii"))
        self._root.mkdir(parents=True, exist_ok=True)

    def put_source(self, document_id: str, data: bytes) -> None:
        self._write(document_id, "source.bin", data)

    def put_markdown(self, document_id: str, markdown: str) -> None:
        self._write(document_id, "extracted.md", markdown.encode("utf-8"))

    def read_source(self, document_id: str) -> bytes:
        return self._read(document_id, "source.bin")

    def read_markdown(self, document_id: str) -> str:
        return self._read(document_id, "extracted.md").decode("utf-8")

    def delete(self, document_id: str) -> bool:
        directory = self._directory(document_id)
        removed = False
        for name in ("source.bin", "extracted.md"):
            path = directory / name
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            pass
        return removed

    def healthy(self) -> bool:
        try:
            with tempfile.NamedTemporaryFile(dir=self._root, delete=True):
                return True
        except OSError:
            return False

    def _write(self, document_id: str, name: str, data: bytes) -> None:
        directory = self._directory(document_id)
        directory.mkdir(parents=True, exist_ok=True)
        encrypted = self._cipher.encrypt(data)
        descriptor, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=directory)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(directory / name)
        finally:
            temp_path.unlink(missing_ok=True)

    def _read(self, document_id: str, name: str) -> bytes:
        try:
            return self._cipher.decrypt((self._directory(document_id) / name).read_bytes())
        except InvalidToken as exc:
            raise ValueError("encrypted document cannot be decrypted") from exc

    def _directory(self, document_id: str) -> Path:
        if not document_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in document_id
        ):
            raise ValueError("document_id is not an opaque safe identifier")
        directory = (self._root / document_id).resolve()
        if directory.parent != self._root:
            raise ValueError("document path escapes store root")
        return directory
