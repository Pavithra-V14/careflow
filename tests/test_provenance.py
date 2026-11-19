"""Decision provenance (migration 0095, spec 9.5), OFFLINE: the rows are closed kinds, ids and versions; nothing that is not an id or a hash can be stored. The database half is in tests/test_rls_triage_live.py."""

import uuid
from pathlib import Path

import pytest

from careflow import provenance_sql as p

ROOT = Path(__file__).resolve().parent.parent
DOC, PAT = uuid.uuid4(), uuid.uuid4()


def test_items_are_the_document_the_patient_each_card_with_its_version_and_the_rule_set():
    items = p.items_for(document_id=DOC, patient_id=PAT, candidates=[{"card_id": "c1", "version": 2, "extra": "ignored text"}], rule_set_hash="f" * 64, fired_rules=[("r1", 3)])
    assert items == [("document", str(DOC), None), ("patient", str(PAT), None), ("card", "c1", 2), ("rule_set", "f" * 64, None), ("rule", "r1", 3)]
    assert p.items_for(document_id=DOC, patient_id=PAT, candidates=[{"card_id": "c1", "version": 2}] * 3, rule_set_hash=None) == [("document", str(DOC), None), ("patient", str(PAT), None), ("card", "c1", 2)]  # no duplicate, no rule set when none


def test_nothing_that_is_not_an_id_or_a_hash_is_accepted():
    for kind, ref, version in (("note", "x", None), ("card", "has a space", 1), ("card", "Chest pain on exertion, 58 yr old", 1), ("card", "", 1), ("card", "x" * 101, 1), ("card", "-lead", 1),
                               ("card", "c1", -1), ("card", "c1", True), ("card", "c1", "2"), ("card", 5, 1), ("card", "line\nbreak", 1)):
        with pytest.raises(p.ProvenanceError):
            p.check_item(kind, ref, version)
    assert p.check_item("card", "knee-pain_v2:a.b", 0) == ("card", "knee-pain_v2:a.b", 0)


def test_too_many_rows_are_refused():
    with pytest.raises(p.ProvenanceError):
        p.items_for(document_id=DOC, patient_id=PAT, candidates=[{"card_id": f"c{i}", "version": 1} for i in range(p.MAX_ITEMS)], rule_set_hash=None)


def test_migration_0095_is_append_only_forced_rls_ids_only_with_an_insert_only_grant():
    sql = (ROOT / "db" / "migrations" / "0095_decision_provenance.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "force row level security" in code and "agents.append_only()" in code and code.count("create policy") == 2
    assert "grant insert (clinic_id, origin, proposal_id, kind, ref, version)" in code and "grant update" not in code and "grant delete" not in code
    assert "check (ref ~" in code and "kind in ('document', 'patient', 'card', 'rule_set', 'rule')" in code
