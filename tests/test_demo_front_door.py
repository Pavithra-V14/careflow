"""The DEMO profile's FAKE Front Door components (careflow/demo_front_door.py), offline. No database, no network, no provider.

Proves: the components exist ONLY with profile demo + data_policy synthetic_only + the demo switch on + loaded keys; in every other combination they are None and the referral run and review routes stay
503 with no handler registered; the fakes are labelled and fictional and ground their own claims; nothing here touches the hosted providers or storage; and the demo values come from the labelled pack.
"""

import base64
import os
import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from careflow import demo_front_door as df
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient
from careflow.crypto import DemographicVault
from careflow.referral import ExtractedReferral
from careflow.rules import RedFlagEngine

PROJECT = "https://example-project.supabase.co"
DEMO_ENV = {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only", "CAREFLOW_DEMO_PHASE3": "1"}


def vault():
    return DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})


def build(env, profile, *, v=True, kv=1):
    return df.components_from_environment(env, profile, vault=vault() if v else None, key_version=kv)


def test_the_components_exist_only_in_the_demo_profile_with_synthetic_data_the_switch_on_and_keys():
    built = build(DEMO_ENV, "demo")
    assert built is not None and built.profile == "demo" and isinstance(built.engine, RedFlagEngine)
    for env, profile, kwargs in (
        (DEMO_ENV, "clinical", {}),
        ({**DEMO_ENV, "CAREFLOW_PROFILE": "clinical"}, "clinical", {}),
        ({k: v for k, v in DEMO_ENV.items() if k != "CAREFLOW_DATA_POLICY"}, "demo", {}),
        ({**DEMO_ENV, "CAREFLOW_DATA_POLICY": "phi_approved"}, "demo", {}),
        ({k: v for k, v in DEMO_ENV.items() if k != "CAREFLOW_DEMO_PHASE3"}, "demo", {}),
        ({**DEMO_ENV, "CAREFLOW_DEMO_PHASE3": "0"}, "demo", {}),
        (DEMO_ENV, "demo", {"v": False}),
        (DEMO_ENV, "demo", {"kv": None}),
    ):
        assert build(env, profile, **kwargs) is None, (env, profile, kwargs)


def test_the_values_come_from_the_labelled_pack_and_the_real_pieces_are_the_real_ones():
    from careflow.dead_letters import SqlDeadLetterTaskWriter
    from careflow.mpi import PatientIngestionPipeline
    from careflow.triage_store import (
        SqlHoldTaskWriter,
        SqlReviewRecorder,
        SqlTriageProposer,
    )

    built = build(DEMO_ENV, "demo")
    assert built is not None
    assert isinstance(built.ingestor, PatientIngestionPipeline) and isinstance(built.proposer, SqlTriageProposer) and isinstance(built.recorder, SqlReviewRecorder)
    assert isinstance(built.holds, SqlHoldTaskWriter) and isinstance(built.dead_letters, SqlDeadLetterTaskWriter)
    assert built.retrieval_scope == {"domain": "demo"}
    import yaml

    from careflow import demo_phase3

    pack = yaml.safe_load(demo_phase3.DEMO_FILE.read_text(encoding="utf-8"))["settings"]
    for key in ("triage_task_role", "triage_task_priority", "hold_task_priority", "dead_letter_task_priority"):
        assert pack[key]["label"] == demo_phase3.DEMO_LABEL  # every value the demo supplies is labelled "DEMO ONLY - NOT A CLINICAL DECISION"


def test_the_module_touches_no_hosted_provider_no_storage_and_no_network():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(df.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"httpx", "requests", "careflow.adapters.gemini", "careflow.adapters.openrouter", "careflow.adapters.sarvam", "careflow.adapters.storage", "careflow.provider_setup", "careflow.gateway"}
    assert not (imported & forbidden), imported & forbidden


def test_the_fictional_letters_are_grounded_by_their_own_extractor():
    import asyncio

    people = {name: (dob, phone) for name, dob, phone in df.DEMO_PEOPLE}
    for index in range(len(df.DEMO_PEOPLE)):
        document = uuid.UUID(int=index)  # int % 5 == index
        letter = df.demo_letter(document)
        assert "DEMO, fictional" in letter
        claim = asyncio.run(df.DemoExtractor().extract(letter, None, clinic_id=uuid.uuid4()))
        dob, phone = people[claim.name]
        assert claim.dob == dob and claim.phone == phone
        for value, quote in ((claim.name, claim.name_quote), (claim.dob, claim.dob_quote), (claim.phone, claim.phone_quote)):
            if value is not None:
                assert quote in letter and value in quote.replace("Patient: ", "").replace("DOB ", "").replace("Mobile ", "")  # the quote IS in the letter and carries the value
    assert asyncio.run(df.DemoExtractor().extract("not a letter", None, clinic_id=uuid.uuid4())) == ExtractedReferral()  # nothing claimed: a person decides
    assert asyncio.run(df.DemoTexts().text_for(uuid.uuid4()))


def test_the_retriever_proposes_only_the_two_fictional_card_ids():
    import asyncio

    got = asyncio.run(df.DemoRetriever().retrieve("anything", scope={"domain": "demo"}))
    assert got.abstained is False and [(c.card_id, c.version) for c in got.candidates] == list(df.DEMO_CARDS)
    assert all(c.card_id.startswith("demo-synthetic-card-") for c in got.candidates)


def test_the_seed_script_uses_the_same_fictional_people():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "seed_dev_demo_data.py"
    spec = importlib.util.spec_from_file_location("seed_dev_demo_data_for_people", path)
    seed = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["seed_dev_demo_data_for_people"] = seed
    spec.loader.exec_module(seed)  # type: ignore[union-attr]
    first_five = [(name, dob, phone[3:] if phone else None) for name, dob, phone in seed.PATIENTS[:5]]
    assert first_five == list(df.DEMO_PEOPLE)


# ---- the app: wired in the demo profile, unwired (503) everywhere else ------------------------------------------------------------------------------------------


def make_app(monkeypatch, env):
    for name in ("CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY", "CAREFLOW_DEMO_PHASE3"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    claims = {"sub": str(uuid.uuid4()), "role": "authenticated"}

    async def fake_db(_c=Depends(verified_claims)):
        yield object()

    async def fake_claims():
        return claims

    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(), vault=vault())  # type: ignore[arg-type]
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return app


def test_the_demo_profile_wires_the_fake_components_into_the_app(monkeypatch):
    app = make_app(monkeypatch, DEMO_ENV)
    with TestClient(app):
        assert app.state.front_door is not None and app.state.front_door.profile == "demo"


@pytest.mark.parametrize(
    "env",
    [
        {"CAREFLOW_PROFILE": "clinical", "CAREFLOW_DATA_POLICY": "synthetic_only", "CAREFLOW_DEMO_PHASE3": "0"},
        {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"},
        {},
    ],
)
def test_any_other_configuration_leaves_the_components_unwired_and_the_routes_503(monkeypatch, env):
    app = make_app(monkeypatch, env)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert app.state.front_door is None
        document = uuid.uuid4()
        run = c.post(f"/v1/staff/referrals/{document}/run", headers={"X-Idempotency-Key": "k"})
        review = c.post(f"/v1/staff/referrals/{document}/review", headers={"X-Idempotency-Key": "k"}, json={"decision": "approve"})
        assert run.status_code == 503 and review.status_code == 503


def test_the_switch_on_without_synthetic_only_refuses_to_start_the_app(monkeypatch):
    from careflow import demo_phase3

    with pytest.raises(demo_phase3.DemoConfigRefused):
        make_app(monkeypatch, {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DEMO_PHASE3": "1"})  # the existing guard is stricter than 503: the app does not start


def test_the_demo_switch_in_the_clinical_profile_is_still_refused_at_build_time(monkeypatch):
    from careflow import demo_phase3

    with pytest.raises(demo_phase3.DemoConfigRefused):
        demo_phase3.refuse_demo_outside_demo({"CAREFLOW_DEMO_PHASE3": "1", "CAREFLOW_DATA_POLICY": "synthetic_only"}, "clinical")


# ---- the seeded DEMO draft rule: usable only in the demo profile, approved by nobody ----------------------------------------------------------------------------


def _draft_row():
    return {"id": uuid.uuid4(), "clinic_id": uuid.uuid4(), "rule_code": df.DEMO_RULE_CODE, "version_id": 1, "status": "draft_unreviewed", "phrases": list(df.DEMO_RULE_PHRASES), "patterns": []}


def test_the_clinical_profile_ignores_the_draft_and_holds_every_letter_as_unassessable_while_only_the_demo_profile_uses_it():
    from careflow import rules

    row = _draft_row()
    letters = [df.demo_letter(uuid.UUID(int=i)) for i in range(len(df.DEMO_PEOPLE))] + [f"a letter that names {df.DEMO_RULE_PHRASES[0]} in it"]

    chosen, passed = rules.select_active_versions([row], "clinical")
    assert chosen == [] and passed == 1  # a draft is passed over, and counted
    clinical = rules.RuleSet(rules=(), profile="clinical", drafts_excluded=passed)
    for letter in letters:
        assert rules.evaluate_with(clinical, letter).outcome == "unassessable", letter  # unknown is not clear: even the letter that contains a token is NOT screened

    chosen, passed = rules.select_active_versions([row], "demo")
    compiled, problem = rules.compile_rule(row)
    assert [c["rule_code"] for c in chosen] == [df.DEMO_RULE_CODE] and passed == 0 and problem is None and compiled is not None
    demo = rules.RuleSet(rules=(compiled,), profile="demo", rule_set_hash=rules.rule_set_hash(chosen))
    assert [rules.evaluate_with(demo, letter).outcome for letter in letters[:-1]] == ["clear"] * len(df.DEMO_PEOPLE)  # the fictional letters contain no token
    assert rules.evaluate_with(demo, letters[-1]).outcome == "red_flag"  # and a letter that does is flagged


def test_the_demo_rule_tokens_are_invented_and_non_medical():
    import re

    assert df.DEMO_RULE_PHRASES and all(re.fullmatch(r"zzdemo[a-z]+", p) for p in df.DEMO_RULE_PHRASES)  # nonsense tokens: nothing a clinician would recognise as a term


def test_the_seed_inserts_the_rule_as_a_draft_and_records_no_approval_for_anyone():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "scripts" / "seed_dev_demo_data.py").read_text(encoding="utf-8")
    import ast

    code = ast.unparse(ast.parse(source).body[1:])  # the code without the module docstring, however the docstring is laid out
    assert "insert into kb.red_flag_rules" in code and "'draft_unreviewed'" in code
    assert "'approved'" not in code and "approved_by_1" not in code and "approved_by_2" not in code and "approved_at" not in code and "authored_by" not in code
