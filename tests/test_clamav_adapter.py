"""The ClamAV adapter speaks clamd's socket protocol (careflow/adapters/clamav.py). A FAKE clamd runs in this process on loopback: no ClamAV, no Docker, no network beyond 127.0.0.1. The 'virus' is an
invented marker, not a real signature or test file. Offline."""

import asyncio
import struct

import pytest

from careflow.adapters.clamav import CHUNK, ClamAvScanner, parse_address
from careflow.malware import NOT_SCANNED, scan_or_hold

pytestmark = pytest.mark.asyncio
MARKER = b"FAKE-VIRUS-MARKER-0001"
VERSION = b"ClamAV 1.9.9/99999/Mon Jan  1 00:00:00 2099\0"


class FakeClamd:
    """Implements the two commands. `reply` overrides the INSTREAM answer; `behaviour` can drop the connection, stall, or send junk."""

    def __init__(self, reply=None, *, behaviour="normal", version=VERSION):
        self.reply, self.behaviour, self.version = reply, behaviour, version
        self.commands, self.received, self.chunk_sizes, self.server = [], b"", [], None
        self.port = 0

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    @property
    def address(self):
        return f"127.0.0.1:{self.port}"

    async def _handle(self, reader, writer):
        try:
            command = await reader.readuntil(b"\0")
            self.commands.append(command)
            if self.behaviour == "drop":
                return
            if self.behaviour == "stall":
                await asyncio.sleep(1.0)
                return
            if command == b"zVERSION\0":
                writer.write(self.version)
            elif command == b"zINSTREAM\0":
                data = b""
                while True:
                    (size,) = struct.unpack(">I", await reader.readexactly(4))
                    if size == 0:
                        break
                    self.chunk_sizes.append(size)
                    data += await reader.readexactly(size)
                self.received = data
                if self.behaviour == "junk":
                    writer.write(b"what is this\0")
                elif self.reply is not None:
                    writer.write(self.reply)
                elif MARKER in data:
                    writer.write(b"stream: Test.Fake.Marker-1 FOUND\0")
                else:
                    writer.write(b"stream: OK\0")
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def test_the_address_forms():
    assert parse_address("127.0.0.1:3310") == ("127.0.0.1", 3310) and parse_address("unix:/run/clamd.sock") == ("unix", "/run/clamd.sock")
    for bad in ("", "nohost", "host:", "host:abc", "host:0", "host:70000", "unix:"):
        with pytest.raises(ValueError):
            parse_address(bad)


async def test_a_clean_file_is_clean_and_the_bytes_arrive_framed_in_chunks_and_unchanged():
    content = bytes(range(256)) * 100  # 25,600 bytes: several chunks
    async with FakeClamd() as clamd:
        result = await ClamAvScanner(clamd.address).scan(content)
    assert (result.verdict, result.scanner) == ("clean", "clamav") and result.signature_version.startswith("ClamAV 1.9.9/99999")
    assert clamd.received == content and clamd.chunk_sizes == [CHUNK, CHUNK, CHUNK, len(content) - 3 * CHUNK]
    assert clamd.commands[0] == b"zINSTREAM\0" and b"zVERSION\0" in clamd.commands


async def test_a_found_reply_is_infected_and_the_signature_name_is_not_kept():
    async with FakeClamd() as clamd:
        result = await ClamAvScanner(clamd.address).scan(b"a letter " + MARKER)
    assert result.verdict == "infected" and "Marker" not in repr(result) and result.signature_version


@pytest.mark.parametrize(
    "reply",
    [b"stream: INSTREAM size limit exceeded. ERROR\0", b"stream: lstat() failed ERROR\0", b"stream: OK extra\0", b"OK\0", b"stream: FOUND\0", b"stream: \0", b"\0", b"stream: ok\0"],
)
async def test_only_an_exact_ok_is_clean_every_other_reply_is_not_scanned_or_infected(reply):
    async with FakeClamd(reply) as clamd:
        result = await ClamAvScanner(clamd.address).scan(b"some bytes")
    assert result.verdict != "clean", reply
    assert result.verdict in {"not_scanned"}, reply


async def test_a_junk_reply_a_dropped_connection_and_a_stall_are_not_scanned():
    for behaviour in ("junk", "drop"):
        async with FakeClamd(behaviour=behaviour) as clamd:
            assert (await ClamAvScanner(clamd.address, timeout=2).scan(b"x")).verdict == "not_scanned", behaviour
    async with FakeClamd(behaviour="stall") as clamd:
        assert (await ClamAvScanner(clamd.address, timeout=0.3).scan(b"x")).verdict == "not_scanned"


async def test_a_daemon_that_is_not_there_is_not_scanned():
    assert (await ClamAvScanner("127.0.0.1:9", timeout=2).scan(b"x")).verdict == "not_scanned"  # nothing listens on the discard port


async def test_an_unreadable_version_means_not_clean():
    async with FakeClamd(version=b"garbage\0") as clamd:
        assert (await ClamAvScanner(clamd.address).scan(b"x")).verdict == "not_scanned"


async def test_empty_non_bytes_and_oversized_input_is_refused_before_any_connection():
    async with FakeClamd() as clamd:
        scanner = ClamAvScanner(clamd.address, max_bytes=10)
        for bad in (b"", "text", None, b"x" * 11):
            assert await scanner.scan(bad) == NOT_SCANNED  # type: ignore[arg-type]
        assert clamd.commands == []  # it never connected
        assert (await scanner.scan(b"x" * 10)).verdict == "clean"  # at the limit is fine


async def test_through_scan_or_hold_a_failure_is_still_not_scanned_never_clean():
    async with FakeClamd(behaviour="junk") as clamd:
        assert (await scan_or_hold(ClamAvScanner(clamd.address), b"letter")).verdict == "not_scanned"
    async with FakeClamd() as clamd:
        assert (await scan_or_hold(ClamAvScanner(clamd.address), b"letter")).clean


def test_bad_settings_are_refused():
    for kwargs in ({"max_bytes": 0}, {"timeout": 0}):
        with pytest.raises(ValueError):
            ClamAvScanner("127.0.0.1:3310", **kwargs)
