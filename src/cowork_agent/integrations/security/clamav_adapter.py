"""Asynchronous ClamAV Antivirus Daemon Adapter (INSTREAM / UNIX & TCP Socket)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
from pathlib import Path
from typing import Final

from cowork_agent.domain.target_contracts import (
    AttachmentSafetyReport,
    ThreatCategory,
    ThreatLevel,
)

logger = logging.getLogger(__name__)

# Default streaming chunk size (64 KB)
CHUNK_SIZE: Final[int] = 64 * 1024


class ClamAVScanner:
    """Production asynchronous ClamAV daemon client communicating via INSTREAM protocol."""

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 3310,
        socket_path: str = "",
        timeout_seconds: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._socket_path = socket_path
        self._timeout = timeout_seconds
        self._enabled = enabled

    async def _open_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a socket connection to clamd (UNIX domain socket or TCP)."""
        if self._socket_path and Path(self._socket_path).exists():
            return await asyncio.open_unix_connection(self._socket_path)
        return await asyncio.open_connection(self._host, self._port)

    async def ping(self) -> bool:
        """Send PING command to ClamAV daemon and verify PONG response."""
        if not self._enabled:
            return False

        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await self._open_connection()
                try:
                    writer.write(b"zPING\0")
                    await writer.drain()
                    response = await reader.read(64)
                    return b"PONG" in response
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (TimeoutError, OSError, ConnectionError) as exc:
            logger.debug("ClamAV ping failed (%s:%s): %s", self._host, self._port, exc)
            return False

    async def get_version(self) -> str | None:
        """Retrieve ClamAV engine version string."""
        if not self._enabled:
            return None

        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await self._open_connection()
                try:
                    writer.write(b"VERSION\n")
                    await writer.drain()
                    response = await reader.read(256)
                    return response.decode("utf-8", errors="ignore").strip()
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (TimeoutError, OSError, ConnectionError) as exc:
            logger.debug("ClamAV version check failed: %s", exc)
            return None

    async def scan_bytes(
        self, content: bytes, filename: str = ""
    ) -> AttachmentSafetyReport:
        """Scan in-memory byte buffer via clamd zINSTREAM command."""
        sha256_hash = hashlib.sha256(content).hexdigest()

        if not self._enabled:
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256_hash,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason="ClamAV scan disabled by configuration",
            )

        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await self._open_connection()
                try:
                    # 1. Initiate INSTREAM session
                    writer.write(b"zINSTREAM\0")
                    await writer.drain()

                    # 2. Stream content in sized chunks
                    for offset in range(0, len(content), CHUNK_SIZE):
                        chunk = content[offset : offset + CHUNK_SIZE]
                        header = struct.pack(">I", len(chunk))
                        writer.write(header + chunk)
                        await writer.drain()

                    # 3. Terminate stream with zero length chunk
                    writer.write(struct.pack(">I", 0))
                    await writer.drain()

                    # 4. Read scan response
                    raw_response = await reader.read(512)
                    response_text = raw_response.decode("utf-8", errors="ignore").strip()

                finally:
                    writer.close()
                    await writer.wait_closed()

            # Parse ClamAV scan response:
            # Examples:
            # - "stream: OK"
            # - "stream: Eicar-Signature FOUND"
            # - "stream: Win.Trojan.Agent FOUND"
            if "FOUND" in response_text:
                virus_name = response_text.replace("stream:", "").replace("FOUND", "").strip()
                logger.warning("ClamAV detected malware in %s: %s", filename, virus_name)
                return AttachmentSafetyReport(
                    filename=filename,
                    sha256=sha256_hash,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.MALICIOUS,
                    threat_category=ThreatCategory.MALWARE,
                    is_safe_to_extract=False,
                    reason=f"ClamAV detected malware signature: {virus_name}",
                )

            if "ERROR" in response_text:
                logger.warning("ClamAV returned scan error for %s: %s", filename, response_text)
                return AttachmentSafetyReport(
                    filename=filename,
                    sha256=sha256_hash,
                    detected_mime_type="application/octet-stream",
                    threat_level=ThreatLevel.CLEAN,
                    threat_category=ThreatCategory.NONE,
                    is_safe_to_extract=True,
                    reason=f"ClamAV error: {response_text}",
                )

            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256_hash,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason="ClamAV: OK (no virus signatures found)",
            )

        except (TimeoutError, OSError, ConnectionError) as exc:
            logger.debug("ClamAV scan connection failed for %s: %s", filename, exc)
            return AttachmentSafetyReport(
                filename=filename,
                sha256=sha256_hash,
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.CLEAN,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=True,
                reason=f"ClamAV unavailable fallback: {exc}",
            )

    async def scan_file(
        self, file_path: Path | str, original_filename: str | None = None
    ) -> AttachmentSafetyReport:
        """Read file from path and scan via clamd."""
        path = Path(file_path)
        if not path.exists():
            return AttachmentSafetyReport(
                filename=original_filename or path.name,
                sha256="",
                detected_mime_type="application/octet-stream",
                threat_level=ThreatLevel.BLOCKED,
                threat_category=ThreatCategory.NONE,
                is_safe_to_extract=False,
                reason=f"File not found on disk: {path}",
            )

        content = path.read_bytes()
        return await self.scan_bytes(content, filename=original_filename or path.name)
