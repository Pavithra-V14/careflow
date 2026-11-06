"""The scanner worker (BUILD-SPEC.md 11.5): rescan documents whose last verdict used an OLDER signature database, because signatures change. It works over the malware-scanner PORT (careflow/malware.py), so it runs
against the fake scanner or the ClamAV client (careflow/adapters/clamav.py) unchanged; where the real daemon runs is the owner's decision (checklist 2) and nothing here starts one.

The rule of careflow.malware holds: only an explicit `clean` is clean; a failure to scan is `not_scanned`, recorded as such, never as clean. One document that cannot be read does not stop the rest, and a
document whose last verdict already used the CURRENT signature version is skipped. The caller supplies how documents are listed, read and recorded (ports), so the worker holds no storage knowledge and no
patient data: it sees an opaque id and bytes it never logs.
"""

from collections.abc import Awaitable, Callable, Iterable

from careflow.malware import MalwareScannerPort, ScanResult, scan_or_hold


async def rescan_batch(
    scanner: MalwareScannerPort,
    documents: Iterable[tuple[str, str]],
    read_bytes: Callable[[str], Awaitable[bytes]],
    record: Callable[[str, ScanResult], Awaitable[None]],
    *,
    current_signature: str,
) -> dict[str, int]:
    """`documents` is [(document id, signature version of the last verdict, or "" for none)]. Returns counts by verdict word plus `skipped` (already current) and `unreadable`."""
    counts = {"clean": 0, "infected": 0, "not_scanned": 0, "skipped": 0, "unreadable": 0}
    if not current_signature:
        raise ValueError("the current signature version is required: without it nothing can be called current")
    for document_id, last_signature in documents:
        if last_signature == current_signature:
            counts["skipped"] += 1
            continue
        try:
            content = await read_bytes(document_id)
        except Exception:  # noqa: BLE001 - an unreadable document is counted, never guessed clean, and never stops the batch
            counts["unreadable"] += 1
            continue
        result = await scan_or_hold(scanner, content)
        await record(document_id, result)
        counts[result.verdict] += 1
    return counts
