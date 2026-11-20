"""scripts/demo_smoke.py: the checks themselves (owner instruction 2026-11-20), offline. The judgements are pure; the reads are select-only; nothing it prints can carry a secret."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "demo_smoke.py"
spec = importlib.util.spec_from_file_location("demo_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["demo_smoke"] = smoke
spec.loader.exec_module(smoke)  # type: ignore[union-attr]

CLINIC = "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11"
GOOD = {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"
HOST = "abcdefgh.supabase.co"
SECRET = "sk-this-must-never-be-printed"
K_GEMINI, K_OPENROUTER, K_SARVAM = smoke.KEY_NAMES  # the names come from the script, so no test file spells a provider key variable


def run(tmp_path, stdin="", **over):
    base = {**GOOD, "SUPABASE_URL": URL, "DATABASE_URL_MIGRATOR": DB, "CAREFLOW_DEV_PROJECT_REF": "abcdefgh", K_GEMINI: SECRET}
    base.update(over)
    env_file = tmp_path / "smoke.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in base.items() if v is not None) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--clinic-id", CLINIC, "--env-file", str(env_file)], input=stdin, capture_output=True, text=True, timeout=90, check=False)


def test_it_refuses_before_any_connection_outside_dev_demo_synthetic_and_without_the_typed_host(tmp_path):
    results = [run(tmp_path, CAREFLOW_ENV="ci"), run(tmp_path, CAREFLOW_PROFILE="clinical"), run(tmp_path, CAREFLOW_DATA_POLICY=None), run(tmp_path, DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")),
               run(tmp_path, stdin="wrong.host\n"), run(tmp_path, stdin="")]
    assert all(r.returncode == 1 for r in results)
    assert f"target project host: {HOST}" in results[4].stdout and "no host was typed" in results[5].stdout
    for r in results:
        out = r.stdout + r.stderr
        assert "not-a-real-password" not in out and SECRET not in out and "Traceback" not in r.stderr


def test_roles_need_every_required_role_live():
    full = dict.fromkeys(smoke.REQUIRED_ROLES, 1)
    assert smoke.judge_roles(full)[1] is True
    name, ok, detail = smoke.judge_roles({**full, "doctor": 0})
    assert not ok and "doctor" in detail and "front_desk 1" in detail and name == "staff roles exist"
    assert not smoke.judge_roles({})[1]


def test_labels_pass_only_with_some_labelled_and_none_unlabelled():
    assert smoke.judge_labels({"a": (3, 0), "b": (1, 0)}) == ("demo placeholders carry their labels", True, "4 labelled row(s), 0 unlabelled")
    assert not smoke.judge_labels({"a": (3, 1)})[1] and "no demo label" in smoke.judge_labels({"a": (3, 1)})[2]
    assert not smoke.judge_labels({"a": (0, 0)})[1] and "seed_demo_placeholders" in smoke.judge_labels({"a": (0, 0)})[2]


def test_cards_and_search_judgements():
    expected = {f"demo-card-{i}" for i in range(6)}
    assert smoke.judge_cards(expected | {"other"}, expected, {"fake"})[1] is True
    assert not smoke.judge_cards(set(list(expected)[:5]), expected, {"fake"})[1]
    assert smoke.judge_search(True, False, True)[1] is True
    assert not smoke.judge_search(False, True, True)[1] and "abstained" in smoke.judge_search(False, True, True)[2]
    assert not smoke.judge_search(False, False, True)[1]
    assert not smoke.judge_search(True, False, False)[1] and "hosted model" in smoke.judge_search(True, False, False)[2]


def test_keys_and_ceilings_are_required_only_with_real_models_and_never_show_a_value():
    off = {K_GEMINI: SECRET}
    assert smoke.judge_keys(off)[1] is True and smoke.judge_ceilings(off)[1] is True
    on = {"CAREFLOW_DEMO_REAL_MODELS": "on", K_GEMINI: SECRET, K_OPENROUTER: SECRET}
    _name, ok, detail = smoke.judge_keys(on)
    assert not ok and K_SARVAM in detail and "2 of 3" in detail and SECRET not in detail
    assert smoke.judge_keys({**on, K_SARVAM: "x"})[1] is True
    ceilings = dict.fromkeys(smoke.CEILING_NAMES, "0.5")
    assert smoke.judge_ceilings({"CAREFLOW_DEMO_REAL_MODELS": "on", **ceilings})[1] is True
    for bad in ("", "abc", "0", "-1", "nan", "inf"):
        result = smoke.judge_ceilings({"CAREFLOW_DEMO_REAL_MODELS": "on", **ceilings, "CAREFLOW_SPEND_CEILING_VLM_USD": bad})
        assert not result[1] and "CAREFLOW_SPEND_CEILING_VLM_USD" in result[2]


def test_the_switches_must_match_section_14():
    base = {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
    assert smoke.judge_switches(base)[1] is True
    assert smoke.judge_switches({**base, "CAREFLOW_FAMILY_LINKS": "on", "CAREFLOW_PATIENT_BOOKING": "on"}) == ("the .env switches are consistent with section 14", True, "2 switch(es) on")
    for bad in ("true", "1", "yes", "enabled"):
        assert "CAREFLOW_FAMILY_LINKS is not exactly on" in smoke.judge_switches({**base, "CAREFLOW_FAMILY_LINKS": bad})[2]
    assert not smoke.judge_switches({"CAREFLOW_PROFILE": "clinical", "CAREFLOW_DATA_POLICY": "synthetic_only"})[1]
    assert "needs CAREFLOW_KNOWLEDGE_EDITOR=on" in smoke.judge_switches({**base, "CAREFLOW_VECTOR_REBUILD": "on", "CAREFLOW_KNOWLEDGE_SEARCH": "on"})[2]
    assert "CAREFLOW_KNOWLEDGE_SEARCH=on" in smoke.judge_switches({**base, "CAREFLOW_KNOWLEDGE_EDITOR": "on"})[2]
    assert smoke.judge_switches({**base, "CAREFLOW_KNOWLEDGE_EDITOR": "on", "CAREFLOW_KNOWLEDGE_SEARCH": "on", "CAREFLOW_VECTOR_REBUILD": "on"})[1] is True
    assert "whole number" in smoke.judge_switches({**base, "CAREFLOW_PATIENT_MESSAGING": "on", "CAREFLOW_MESSAGE_MAX_PER_MINUTE": "ten"})[2]


def test_the_rules_before_models_check_passes_and_spends_nothing():
    assert smoke.judge_rules_before_models() == (
        "rules run before any model", True, "a clearance cannot be forged, an empty rule set clears nothing, the extractor requires a clearance",
    )


def test_render_prints_only_pass_fail_lines_and_counts_and_sets_the_exit_status():
    lines: list[str] = []
    assert smoke.render([("a", True, "1 row"), ("b", False, "missing")], lines.append) == 1
    assert lines == ["PASS a: 1 row", "FAIL b: missing", "  what to do: see docs/demo-runbook.md and fix the line above, then run this again", "1 passed, 1 failed"]
    assert smoke.render([("a", True, "x")], lines.append) == 0


def test_the_script_only_reads_and_never_prints_an_environment_value():
    code = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2]
    sql = " ".join(re.findall(r'text\(f?"([^"]+)"', code)).lower()
    assert sql and not re.search(r"\b(insert|update|delete|create|alter|drop|truncate|grant)\b", sql)
    assert "env[" not in "".join(line for line in code.splitlines() if "say(" in line or "print(" in line and "host" not in line)
    assert "knowledge.KnowledgeSearch" in code and "HashingEmbedding" in code and "provider_setup" not in code  # the search uses the local embedder: no provider is called


def test_every_check_has_a_fixed_words_fix_line_that_names_a_command_or_a_setting_and_never_a_value():
    names = {
        "migrations are applied", "the clinic is published", "staff roles exist", "demo placeholders carry their labels", "the six demo cards are indexed", "a knowledge search returns a card",
        "provider keys are present", "spend ceilings are set", "rules run before any model", "the .env switches are consistent with section 14", "no backend runs without the demo profile",
        "portal accounts have the demo origin", "the second factor matches the screens switched on", "the three screen switches are on",
    }
    assert set(smoke.FIXES) == names
    for name, fix in smoke.FIXES.items():
        assert fix and "=" not in fix.replace("NAME=on", "").replace("CAREFLOW_REQUIRE_AAL2", "").replace("CAREFLOW_KNOWLEDGE_SEARCH=on", "").replace("CAREFLOW_DEMO_REAL_MODELS", ""), name


def test_a_backend_already_listening_must_be_the_demo_one():
    assert smoke.judge_backend(False, None)[1] is True
    assert smoke.judge_backend(True, True)[1] is True
    wrong = smoke.judge_backend(True, False)
    assert not wrong[1] and "plain uvicorn" in wrong[2] and "wrong origin" in wrong[2]
    assert not smoke.judge_backend(True, None)[1]


def test_probe_backend_reads_readyz_without_credentials_from_a_local_server():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    def serve(status, payload):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                assert "Authorization" not in self.headers  # no credential is ever sent
                assert self.path == "/readyz"
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    for status, payload, expected in ((200, {"ready": True}, (True, True)), (503, {"ready": False}, (True, False)), (200, {"ready": "yes"}, (True, None)), (200, b"not json", (True, None)), (404, b"nope", (True, None))):
        server = serve(status, payload)
        try:
            assert smoke.probe_backend(server.server_port) == expected, (status, payload)
        finally:
            server.shutdown()
    free = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = free.server_port
    free.server_close()
    assert smoke.probe_backend(port) == (False, None)  # nothing listening


def test_the_email_sign_in_needs_demo_origin_accounts():
    on = {"CAREFLOW_DEMO_EMAIL_SIGNIN": "on"}
    assert smoke.judge_live_accounts({}, 5)[1] is True  # not applicable while the sign-in is off
    assert smoke.judge_live_accounts(on, 0)[1] is True
    result = smoke.judge_live_accounts(on, 2)
    assert not result[1] and "2 patient account(s) have origin live" in result[2]


def test_the_second_factor_must_match_the_screens_that_need_it_at_the_database():
    off = {"CAREFLOW_REQUIRE_AAL2": "false"}
    assert smoke.judge_second_factor({})[1] is True and smoke.judge_second_factor({"CAREFLOW_REQUIRE_AAL2": "true"})[1] is True
    assert smoke.judge_second_factor(off)[1] is True  # off and nothing that needs it
    for switch in smoke.AAL2_SCREENS:
        result = smoke.judge_second_factor({**off, switch: "on"})
        assert not result[1] and switch in result[2], switch
    for spelling in ("0", "off", "no", "False"):
        assert not smoke.judge_second_factor({"CAREFLOW_REQUIRE_AAL2": spelling, "CAREFLOW_FAMILY_LINKS": "on"})[1]


def test_the_three_screen_switches_must_be_on():
    all_on = dict.fromkeys(smoke.SCREEN_SWITCHES, "on")
    assert smoke.judge_screen_switches(all_on) == ("the three screen switches are on", True, "3 of 3 on")
    result = smoke.judge_screen_switches({**all_on, "CAREFLOW_FAMILY_LINKS": "off"})
    assert not result[1] and "CAREFLOW_FAMILY_LINKS" in result[2] and "2 of 3" in result[2]
    assert not smoke.judge_screen_switches({})[1]
