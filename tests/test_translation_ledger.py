"""Sarvam translation spend in the DURABLE ledger (migration 0086) and the hardening of the card vectors (migration 0087), OFFLINE: the adapter's recording behaviour over an in-memory ledger and a recorded-shape fake
transport, and a static read of both migrations. The database halves are in tests/test_rls_spend_and_approval_live.py and tests/test_knowledge_db_live.py. NO REAL PROVIDER IS CALLED. Synthetic words only."""

import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from careflow.adapters.sarvam import SarvamTranslation
from careflow.gateway import InMemorySpendLedger, RoleDailyCeilings, SpendCeilingReached
from careflow.ports import DataClass, PermanentError, TransientError, TranslationRequest
from careflow.provider_setup import build_providers
from tests.test_providers import KEY, Mock, good_env, json_reply

ROOT = Path(__file__).resolve().parent.parent
CLINIC = uuid.uuid4()


def adapter(ledger, *, ceiling="1", price="0.06", body=None):
    mock = Mock(json_reply(body or {"translated_text": "TAMIL-FAKE"}))
    return SarvamTranslation(KEY, timeout_seconds=5, price_per_1k_chars_usd=Decimal(price), ceilings=RoleDailyCeilings({"translation": Decimal(ceiling)}), http=mock.client(), ledger=ledger, origin="test"), mock


@pytest.mark.asyncio
async def test_a_successful_translation_with_a_clinic_is_recorded_in_the_durable_ledger_under_the_translation_role():
    ledger = InMemorySpendLedger()
    sarvam, _ = adapter(ledger)
    text = "x" * 500
    out = await sarvam.translate(TranslationRequest(text, "en", "ta", DataClass.SYNTHETIC, CLINIC))
    assert out.value.text == "TAMIL-FAKE"
    (entry,) = ledger.entries
    assert (entry.clinic_id, entry.role, entry.provider, entry.model_id, entry.call_class, entry.origin) == (CLINIC, "translation", "sarvam", "mayura:v1", "ordinary", "test")
    assert entry.usd_actual == entry.usd_shadow == Decimal("0.030") and entry.idempotency_key.startswith("sarvam-")  # 500 characters at 0.06 per 1,000
    assert await ledger.spent_today(CLINIC, "test") == Decimal("0.030")  # it now counts toward the clinic's daily total
    await sarvam.translate(TranslationRequest(text, "en", "ta", DataClass.SYNTHETIC, CLINIC))
    assert len({e.idempotency_key for e in ledger.entries}) == 2  # every write has its own key


@pytest.mark.asyncio
async def test_nothing_is_recorded_without_a_clinic_a_ledger_or_a_successful_call():
    ledger = InMemorySpendLedger()
    sarvam, _ = adapter(ledger)
    await sarvam.translate(TranslationRequest("hello there", "en", "ta", DataClass.SYNTHETIC))  # no clinic: the in-memory ceiling only
    assert ledger.entries == []
    no_ledger, _ = adapter(None)
    await no_ledger.translate(TranslationRequest("hello there", "en", "ta", DataClass.SYNTHETIC, CLINIC))
    failing, _ = adapter(ledger, body={"nope": 1})
    with pytest.raises(PermanentError):
        await failing.translate(TranslationRequest("hello there", "en", "ta", DataClass.SYNTHETIC, CLINIC))
    refused, mock = adapter(ledger)
    with pytest.raises(PermanentError):
        await refused.translate(TranslationRequest("hello there", "en", "ta", DataClass.PHI, CLINIC))  # patient text is refused before anything
    assert ledger.entries == [] and mock.requests == []


@pytest.mark.asyncio
async def test_a_ceiling_stop_records_nothing_and_a_ledger_that_cannot_record_fails_the_call_visibly():
    ledger = InMemorySpendLedger()
    sarvam, mock = adapter(ledger, ceiling="0.0001")
    with pytest.raises(SpendCeilingReached):
        await sarvam.translate(TranslationRequest("x" * 900, "en", "ta", DataClass.SYNTHETIC, CLINIC))
    assert ledger.entries == [] and mock.requests == []

    class Broken:
        async def record(self, entry):
            raise RuntimeError("ledger down: " + KEY)

        async def spent_today(self, clinic_id, origin):
            return Decimal(0)

    bad, _ = adapter(Broken())
    with pytest.raises(TransientError) as caught:
        await bad.translate(TranslationRequest("hello there", "en", "ta", DataClass.SYNTHETIC, CLINIC))
    assert KEY not in f"{caught.value!s} {caught.value!r}" and "ledger down" not in str(caught.value)  # a paid call that cannot be counted is a failed call, in fixed words


def test_the_built_translation_route_shares_the_gateways_ledger():
    ledger = InMemorySpendLedger()
    built = build_providers(good_env(), profile="demo", ledger=ledger)
    assert built.translation is not None and built.gateway is not None and built.gateway.ledger is ledger and built.translation._ledger is ledger


def test_migration_0086_only_widens_the_ledger_role_check_by_translation():
    sql = (ROOT / "db" / "migrations" / "0086_spend_ledger_translation_role.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    original = (ROOT / "db" / "migrations" / "0016_spend_ledger_and_registry_approval.sql").read_text(encoding="utf-8")
    old = set(re.findall(r"'(\w+)'", re.search(r"role\s+text not null check \(role in \((.*?)\)\)", original, re.DOTALL).group(1)))
    new = set(re.findall(r"'(\w+)'", re.search(r"check \(role in \((.*?)\)\)", code, re.DOTALL).group(1)))
    assert new == old | {"translation"} and old < new  # every old value stays; exactly one is added
    statements = [s.strip() for s in code.split(";") if s.strip()]
    assert len(statements) == 2 and statements[0].startswith("alter table ops.spend_ledger drop constraint spend_ledger_role_check") and statements[1].startswith("alter table ops.spend_ledger add constraint spend_ledger_role_check")
    assert not re.search(r"\b(grant|policy|truncate|delete|update|insert)\b", code, re.IGNORECASE)  # no privilege, no row touched


def test_migration_0087_hardens_the_card_vectors_without_touching_0085():
    sql = (ROOT / "db" / "migrations" / "0087_card_embeddings_hardening.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "rolbypassrls" in code and "extnamespace = 'extensions'::regnamespace" in code and code.index("rolbypassrls") < code.index("create or replace function kb.index_card_embedding")
    assert "card_version <= 100000" in code and "p_card_version > v_max + 1" in code and "errcode = '23514'" in code
    assert code.count("create or replace function") == 3 and "create function" not in code and "drop function" not in code  # same signatures: the EXECUTE grants stay
    assert "n.model_id" not in code  # the current version is global per card
    assert code.count("operator(extensions.<#>) v_vec) = 0") == 2 and code.count("length(p_embedding) > 30000") == 1 and "length(p_query) > 30000" in code
    assert "exception when unique_violation then" in code and "v_prior.audience is distinct from v_aud" in code and "operator(extensions.=)" in code
    for fn in ("index_card_embedding", "search_card_embeddings", "current_card_versions"):
        block = code[code.index(f"create or replace function kb.{fn}") :]
        block = block[: block.index("\n$$;")]
        assert "security definer set search_path = ''" in block and "auth.uid()" in block and "42501" in block
    assert "revoke all on kb.card_embeddings from careflow_app" in code and "'anon', 'authenticated', 'service_role'" in code
    assert not re.search(r"\bgrant\b", code)  # revokes only
    original = (ROOT / "db" / "migrations" / "0085_card_embeddings.sql").read_text(encoding="utf-8")
    assert "p_card_version > v_max" not in original  # (0085 is as merged: the fix is a new migration)


def test_migration_0088_fixes_the_race_handler_the_vector_tolerance_and_the_owner_assertion_only():
    sql = (ROOT / "db" / "migrations" / "0088_card_embeddings_index_race_fix.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert code.count("create or replace function") == 1 and "create function" not in code and not re.search(r"\bgrant\b", code)  # the same signature: the EXECUTE grant stays; nothing new is granted
    handler = code[code.index("exception when unique_violation then") :]
    handler = handler[: handler.index("end;\n    insert into audit.log")]
    for needed in ("v_prior.card_id <> p_card_id", "v_prior.card_version <> p_card_version", "v_prior.model_id <> p_model_id", "order by (e.idempotency_key = v_key) desc"):
        assert needed in handler, needed  # the race answer compares the card, the version and the model, and prefers the key's row
    assert "operator(extensions.=)" not in code and code.count("operator(extensions.<=>) v_vec) > 0.001") == 3  # a replay tolerates a vector within a tiny cosine distance, never an exact float match
    assert "p.proowner" in code and "c.relowner" in code and "rolbypassrls" in code  # the owner of the functions and the table, not only the migrator
    assert "security definer set search_path = ''" in code and "errcode = '42501'" in code and "p_card_version > v_max + 1" in code
