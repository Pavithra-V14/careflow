"""Decision provenance (migration 0095; BUILD-SPEC.md 9.5): the ids and versions of the facts a triage proposal read. IDS AND VERSIONS ONLY, NEVER TEXT.

`items_for` builds the rows from what the proposal already knows; `record` writes them (idempotent: the same fact twice is one row); `decisions_that_read` is the lookup a correction needs ("which proposals read
this id at this version"). Nothing here reads or stores a letter, a name or a value, and `ref` is checked against a pattern that admits only ids and hashes.
"""

import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

KINDS: Final = ("document", "patient", "card", "rule_set", "rule")
_REF: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,99}$")
MAX_ITEMS: Final = 60

Item = tuple[str, str, int | None]


class ProvenanceError(Exception):
    """A provenance row that is not an id and a version (fixed words; the value is never echoed)."""


def check_item(kind: object, ref: object, version: object) -> Item:
    if kind not in KINDS or not isinstance(ref, str) or not _REF.fullmatch(ref):
        raise ProvenanceError("a provenance row is a known kind and an id or hash")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 0):
        raise ProvenanceError("a provenance version is a whole number or none")
    return kind, ref, version  # type: ignore[return-value]


def items_for(*, document_id: uuid.UUID, patient_id: uuid.UUID, candidates: Iterable[Mapping[str, Any]], rule_set_hash: str | None, fired_rules: Iterable[tuple[str, int]] = ()) -> list[Item]:
    """The facts a triage proposal read: the document, the patient, each candidate card with its version, the rule set it was screened against and any rule that fired."""
    items: list[Item] = [check_item("document", str(document_id), None), check_item("patient", str(patient_id), None)]
    for candidate in candidates:
        items.append(check_item("card", str(candidate.get("card_id")), candidate.get("version")))
    if rule_set_hash:
        items.append(check_item("rule_set", rule_set_hash, None))
    for rule_id, version in fired_rules:
        items.append(check_item("rule", str(rule_id), version))
    unique = list(dict.fromkeys(items))
    if len(unique) > MAX_ITEMS:
        raise ProvenanceError("too many provenance rows")
    return unique


async def record(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, proposal_id: uuid.UUID, items: Iterable[Item]) -> int:
    """Write the rows (idempotent). Returns how many were new. The caller's row-level security decides who may."""
    written = 0
    for kind, ref, version in (check_item(*item) for item in items):
        result = await conn.execute(
            text(
                "insert into agents.decision_provenance (clinic_id, origin, proposal_id, kind, ref, version) values (:c, :o, :p, :k, :r, :v) "
                "on conflict (proposal_id, kind, ref, coalesce(version, -1)) do nothing"
            ),
            {"c": clinic_id, "o": origin, "p": proposal_id, "k": kind, "r": ref, "v": version},
        )
        written += result.rowcount or 0
    return written


async def decisions_that_read(conn: AsyncConnection, *, clinic_id: uuid.UUID, kind: str, ref: str, version: int | None = None) -> list[uuid.UUID]:
    """The proposals of a clinic that read this fact (at this version when one is given), oldest first."""
    check_item(kind, ref, version)
    rows = (
        await conn.execute(
            text(
                "select proposal_id from agents.decision_provenance where clinic_id = :c and kind = :k and ref = :r and (cast(:v as integer) is null or version = :v) order by created_at, id"
            ),
            {"c": clinic_id, "k": kind, "r": ref, "v": version},
        )
    ).all()
    return [r.proposal_id for r in rows]
