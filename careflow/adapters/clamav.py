"""A ClamAV scanner adapter that speaks clamd's socket protocol (BUILD-SPEC.md 11.5; docs/design/malware-scanner-options.md, option 1, direction approved 2026-10-30).

NOTHING IS INSTALLED, NOTHING IS WIRED. This is the client half only: it talks to a `clamd` that the owner will run on a machine of their choice (not decided yet), and it is not referenced by the
application's start-up. `app.state.malware_scanner_configured` stays False and the clinical profile stays not ready until a scanner is chosen, wired and shown to answer a test file. The tests use a
fake clamd server in this process; no ClamAV, no Docker, no network beyond loopback.

PROTOCOL (clamd, null-terminated commands, the `z` prefix): `zVERSION\\0` answers `ClamAV <version>/<signature db number>/<date>`; `zINSTREAM\\0` is followed by chunks, each a 4-byte big-endian
length then that many bytes, ended by a zero-length chunk; the reply is `stream: OK`, `stream: <signature name> FOUND` or `stream: <message> ERROR` (null-terminated).

THE RULE (careflow/malware.py): only an explicit OK is `clean`. FOUND is `infected`. EVERYTHING else is `not_scanned`: the daemon unreachable, a timeout, a reply that is not exactly one of those shapes, an
ERROR (including clamd's own size-limit refusal), bytes larger than this adapter's limit (refused BEFORE connecting, so a partly-scanned file is never reported clean), a connection closed early.
Neither the bytes nor the signature NAME that matched are returned, logged or stored (the verdict records only the scanner name and the signature database version).
"""

import asyncio
import struct
from typing import Final

from careflow.malware import NOT_SCANNED, ScanResult

CHUNK: Final = 8192
DEFAULT_MAX_BYTES: Final = 25 * 1024 * 1024  # clamd's default StreamMaxLength; a larger file is not scanned (not_scanned), never partly scanned
_REPLY_LIMIT: Final = 1024


def parse_address(address: str) -> tuple[str, str | int]:
    """`host:port` (TCP) or `unix:/path/to/clamd.sock`. Anything else is a configuration error."""
    if address.startswith("unix:") and len(address) > 5:
        return "unix", address[5:]
    host, sep, port = address.rpartition(":")
    if sep and host and port.isdigit() and 0 < int(port) < 65536:
        return host, int(port)
    raise ValueError("a scanner address is host:port or unix:/path")


class ClamAvScanner:
    name = "clamav"

    def __init__(self, address: str, *, max_bytes: int = DEFAULT_MAX_BYTES, timeout: float = 30.0) -> None:
        self._where = parse_address(address)
        if max_bytes <= 0 or timeout <= 0:
            raise ValueError("the size limit and the timeout must be positive")
        self.max_bytes, self.timeout = max_bytes, timeout

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        host, port = self._where
        if host == "unix":
            return await asyncio.open_unix_connection(str(port))  # type: ignore[attr-defined]
        return await asyncio.open_connection(host, int(port))

    async def _command(self, command: bytes) -> bytes:
        reader, writer = await self._open()
        try:
            writer.write(command)
            await writer.drain()
            return await reader.readuntil(b"\0")
        finally:
            writer.close()

    async def version(self) -> str:
        """The daemon's version string (`ClamAV 1.x/<db number>/<date>`), or "" if it cannot be read. Recorded with the verdict so a later signature update can trigger a rescan."""
        try:
            reply = (await asyncio.wait_for(self._command(b"zVERSION\0"), self.timeout)).rstrip(b"\0").decode("ascii", "replace")
        except (TimeoutError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return ""
        return reply[:200] if reply.startswith("ClamAV") else ""

    async def _instream(self, content: bytes) -> bytes:
        reader, writer = await self._open()
        try:
            writer.write(b"zINSTREAM\0")
            for start in range(0, len(content), CHUNK):
                piece = content[start : start + CHUNK]
                writer.write(struct.pack(">I", len(piece)) + piece)
            writer.write(struct.pack(">I", 0))
            await writer.drain()
            return await reader.readuntil(b"\0")
        finally:
            writer.close()

    async def scan(self, content: bytes) -> ScanResult:
        if not isinstance(content, bytes | bytearray) or not content or len(content) > self.max_bytes:
            return NOT_SCANNED  # empty, not bytes, or too large: refused before any connection
        try:
            reply = await asyncio.wait_for(self._instream(bytes(content)), self.timeout)
        except (TimeoutError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return NOT_SCANNED
        text = reply.rstrip(b"\0").decode("ascii", "replace").strip()
        if len(reply) > _REPLY_LIMIT or not text.startswith("stream:"):
            return NOT_SCANNED
        verdict = text.removeprefix("stream:").strip()
        signature = await self.version()
        if verdict == "OK":
            # An unknown signature version is an unknown state of the scanner: not clean (a later signature update could not trigger a rescan of this file).
            return ScanResult("clean", self.name, signature) if signature else NOT_SCANNED
        if verdict.endswith(" FOUND") and len(verdict) > len(" FOUND"):
            return ScanResult("infected", self.name, signature)  # the signature NAME is deliberately dropped
        return ScanResult("not_scanned", self.name, signature)  # ERROR, size limit, anything unexpected
