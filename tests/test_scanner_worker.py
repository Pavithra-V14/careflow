"""careflow/scanner_worker.py over the real ClamAV client, against the in-process FAKE clamd of tests/test_clamav_adapter.py (loopback only, no ClamAV, no Docker)."""

import pytest

from careflow.adapters.clamav import ClamAvScanner
from careflow.scanner_worker import rescan_batch
from tests.test_clamav_adapter import MARKER, FakeClamd

pytestmark = pytest.mark.asyncio


async def test_old_or_missing_signatures_are_rescanned_current_ones_skipped_and_an_unreadable_document_does_not_stop_the_rest():
    recorded = {}

    async def read(doc):
        if doc == "gone":
            raise OSError("missing")
        return MARKER if doc == "bad" else b"%PDF-1.4 fine"

    async def record(doc, result):
        recorded[doc] = (result.verdict, result.scanner)

    async with FakeClamd() as clamd:
        scanner = ClamAvScanner(clamd.address)
        current = await scanner.version()
        counts = await rescan_batch(scanner, [("fresh", current), ("old", "ClamAV 1.0/1/x"), ("never", ""), ("bad", "ClamAV 1.0/1/x"), ("gone", "")], read, record, current_signature=current)
    assert counts == {"clean": 2, "infected": 1, "not_scanned": 0, "skipped": 1, "unreadable": 1}
    assert recorded == {"old": ("clean", "clamav"), "never": ("clean", "clamav"), "bad": ("infected", "clamav")}


async def test_a_dead_daemon_records_not_scanned_never_clean():
    recorded = {}

    async def read(doc):
        return b"%PDF-1.4 fine"

    async def record(doc, result):
        recorded[doc] = result.verdict

    scanner = ClamAvScanner("127.0.0.1:1", timeout=0.5)
    counts = await rescan_batch(scanner, [("a", ""), ("b", "old")], read, record, current_signature="ClamAV 9/9/x")
    assert counts["not_scanned"] == 2 and counts["clean"] == 0 and recorded == {"a": "not_scanned", "b": "not_scanned"}


async def test_without_a_current_signature_nothing_can_be_called_current():
    async def never(*a):
        raise AssertionError("must not be reached")

    with pytest.raises(ValueError):
        await rescan_batch(ClamAvScanner("127.0.0.1:1"), [("a", "")], never, never, current_signature="")
