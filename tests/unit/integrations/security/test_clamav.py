"""Unit tests for ClamAV Antivirus Daemon Adapter and INSTREAM protocol."""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path

import pytest

from cowork_agent.domain.target_contracts import ThreatCategory, ThreatLevel
from cowork_agent.integrations.security.clamav_adapter import ClamAVScanner
from cowork_agent.integrations.security.fakes import FakeClamAVScanner


class MockClamAVServer:
    """In-process mock TCP server implementing the ClamAV daemon protocol."""

    def __init__(self, response_map: dict[str, bytes] | None = None) -> None:
        self.server: asyncio.Server | None = None
        self.port: int = 0
        self.received_commands: list[bytes] = []
        self.received_chunks: list[bytes] = []
        self.response_map = response_map or {}

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            # Read command
            cmd = await reader.readuntil(b"\0")
            self.received_commands.append(cmd)

            if cmd == b"zPING\0":
                writer.write(b"PONG\0")
                await writer.drain()
            elif cmd.startswith(b"VERSION"):
                writer.write(b"ClamAV 1.4.0/27351/Tue Aug 25 2026\n")
                await writer.drain()
            elif cmd == b"zINSTREAM\0":
                # Read chunks until length == 0
                full_stream = bytearray()
                while True:
                    len_bytes = await reader.readexactly(4)
                    chunk_len = struct.unpack(">I", len_bytes)[0]
                    if chunk_len == 0:
                        break
                    chunk = await reader.readexactly(chunk_len)
                    full_stream.extend(chunk)

                self.received_chunks.append(bytes(full_stream))

                # Check if stream matches any infected pattern
                stream_bytes = bytes(full_stream)
                response = b"stream: OK\0"
                for pattern_str, resp in self.response_map.items():
                    if pattern_str.encode("utf-8") in stream_bytes:
                        response = resp
                        break

                writer.write(response)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self.handle_client, "127.0.0.1", 0
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()


@pytest.mark.asyncio
async def test_clamav_ping_and_version():
    server = MockClamAVServer()
    await server.start()
    try:
        scanner = ClamAVScanner(host="127.0.0.1", port=server.port, timeout_seconds=2.0)
        assert await scanner.ping() is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_clamav_ping_offline_returns_false():
    scanner = ClamAVScanner(host="127.0.0.1", port=65530, timeout_seconds=0.5)
    assert await scanner.ping() is False


@pytest.mark.asyncio
async def test_clamav_scan_clean_file():
    server = MockClamAVServer()
    await server.start()
    try:
        scanner = ClamAVScanner(host="127.0.0.1", port=server.port, timeout_seconds=2.0)
        clean_content = b"%PDF-1.7 This is a clean harmless invoice document."
        report = await scanner.scan_bytes(clean_content, filename="invoice.pdf")

        assert report.threat_level == ThreatLevel.CLEAN
        assert report.threat_category == ThreatCategory.NONE
        assert report.is_safe_to_extract is True
        assert report.filename == "invoice.pdf"
        assert len(server.received_chunks) == 1
        assert server.received_chunks[0] == clean_content
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_clamav_scan_infected_file():
    eicar_str = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    server = MockClamAVServer(
        response_map={eicar_str: b"stream: Win.Test.EICAR_HDB-1 FOUND\0"}
    )
    await server.start()
    try:
        scanner = ClamAVScanner(host="127.0.0.1", port=server.port, timeout_seconds=2.0)
        report = await scanner.scan_bytes(eicar_str.encode("utf-8"), filename="eicar.com")

        assert report.threat_level == ThreatLevel.MALICIOUS
        assert report.threat_category == ThreatCategory.MALWARE
        assert report.is_safe_to_extract is False
        assert "Win.Test.EICAR_HDB-1" in (report.reason or "")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_clamav_scan_file_path(tmp_path: Path):
    server = MockClamAVServer()
    await server.start()
    try:
        scanner = ClamAVScanner(host="127.0.0.1", port=server.port, timeout_seconds=2.0)

        # Existing clean file
        clean_file = tmp_path / "valid.txt"
        clean_file.write_bytes(b"Clean file on disk")
        report = await scanner.scan_file(clean_file)
        assert report.threat_level == ThreatLevel.CLEAN
        assert report.filename == "valid.txt"

        # Missing file
        missing_file = tmp_path / "non_existent.txt"
        missing_report = await scanner.scan_file(missing_file)
        assert missing_report.threat_level == ThreatLevel.BLOCKED
        assert "File not found" in (missing_report.reason or "")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_fake_clamav_scanner():
    fake = FakeClamAVScanner()

    # Ping
    assert await fake.ping() is True
    assert "ClamAV" in (await fake.get_version() or "")

    # EICAR detection
    eicar_bytes = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    report = await fake.scan_bytes(eicar_bytes, filename="eicar.txt")
    assert report.threat_level == ThreatLevel.MALICIOUS
    assert report.threat_category == ThreatCategory.MALWARE
    assert report.is_safe_to_extract is False

    # Clean content
    clean_report = await fake.scan_bytes(b"Hello world", filename="hello.txt")
    assert clean_report.threat_level == ThreatLevel.CLEAN
    assert clean_report.is_safe_to_extract is True

    # Offline fake
    offline_fake = FakeClamAVScanner(is_online=False)
    assert await offline_fake.ping() is False
    assert await offline_fake.get_version() is None
    offline_report = await offline_fake.scan_bytes(b"Some text", filename="test.txt")
    assert offline_report.threat_level == ThreatLevel.CLEAN
