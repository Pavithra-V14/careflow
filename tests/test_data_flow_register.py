"""The data-flow register is a real gate, not a comment (BUILD-SPEC.md §17.6; CLAUDE.md rule 7). Pure."""

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTER = yaml.safe_load((ROOT / "data-flow-register.yaml").read_text(encoding="utf-8"))
CALLS = REGISTER["calls"]

REQUIRED = {
    "id", "service", "host", "source", "data_sent", "data_class", "data_policy",
    "permitted_in_clinical", "approved_by", "approved_on", "status", "appendix_b", "notes",
}
DATA_CLASSES = {"none", "pseudonymous", "patient_ciphertext", "patient_plaintext"}
DATA_POLICIES = {"synthetic_only", "phi_approved"}
PERMITTED = {True, False, "unconfirmed"}
STATUSES = {"proposed", "approved", "retired"}
# Modules that import any of these talk to something outside the process.
NETWORK_ROOTS = {"httpx", "requests", "aiohttp", "urllib", "smtplib", "socket", "websockets", "grpc", "boto3", "twilio", "sendgrid"}


def test_every_entry_is_complete_and_uses_allowed_values():
    assert isinstance(CALLS, list)
    ids = [c["id"] for c in CALLS]
    assert len(ids) == len(set(ids)), "duplicate register ids"
    for c in CALLS:
        assert REQUIRED <= set(c), f"{c.get('id')}: missing {REQUIRED - set(c)}"
        assert set(c) <= REQUIRED, f"{c['id']}: unknown fields {set(c) - REQUIRED}"
        assert c["data_class"] in DATA_CLASSES, c["id"]
        assert c["data_policy"] in DATA_POLICIES, c["id"]
        assert c["permitted_in_clinical"] in PERMITTED, c["id"]
        assert c["status"] in STATUSES, c["id"]
        assert re.match(r"^[a-z0-9][a-z0-9-]*$", c["id"]), c["id"]
        assert (ROOT / c["source"]).is_file(), f"{c['id']}: source {c['source']} does not exist"


def test_clinical_permission_needs_a_named_approver_and_date():
    for c in CALLS:
        if c["permitted_in_clinical"] is True:
            assert c["approved_by"] and c["approved_on"], f"{c['id']} is permitted in clinical with no named approver"
            assert c["status"] == "approved", c["id"]
        if c["data_class"] in {"patient_plaintext"}:
            assert c["permitted_in_clinical"] is not True or c["data_policy"] == "phi_approved", c["id"]


def test_every_module_that_makes_an_outbound_call_has_a_register_entry():
    listed = {c["source"].replace("\\", "/") for c in CALLS}
    for path in sorted((ROOT / "careflow").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        rel = path.relative_to(ROOT).as_posix()
        if roots & NETWORK_ROOTS:
            assert rel in listed, f"{rel} imports {roots & NETWORK_ROOTS} but has no entry in data-flow-register.yaml"


def test_the_database_driver_module_is_registered_too():
    """careflow/db.py opens the database connection (asyncpg through SQLAlchemy), a call out of the process."""
    assert "careflow/db.py" in {c["source"] for c in CALLS}
