"""scripts/run_demo_backend.ps1 (bug found on the owner's machine, 2026-11-20): a carriage return had replaced a backslash-r inside a COMMENT, so PowerShell read the rest of the comment as a command and failed with
"un_demo_backend.ps1 is not recognized". These tests keep that from coming back: no stray control character in any script, every line is a comment or starts with a known statement, the real PowerShell parser
accepts the file when one is installed, and the script's rules (port 8000, refuses outside the synthetic dev demo, never prints a value) are pinned. They cannot run the script against a real machine."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "scripts").glob("*.ps1"))
RUN = ROOT / "scripts" / "run_demo_backend.ps1"
UP = ROOT / "scripts" / "demo_up.ps1"
STATEMENT = re.compile(r"^(param\(|\$ErrorActionPreference|if \(|foreach \(|\$[A-Za-z_:.]+ ?=|Set-Item|Write-Host|&\s|\}|exit\b|try\b|catch\b|else\b|Get-|Read-Host|\$)")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_script_holds_a_stray_control_character(path):
    raw = path.read_bytes()
    assert not re.search(rb"\r(?!\n)|[\x07\x08\x0b\x0c\x1b\x00]", raw), f"{path.name} holds a stray control character (an escape sequence that was turned into a character)"
    assert b"\t" not in raw, f"{path.name} holds a tab character (a backslash-t that was turned into a character)"


def test_every_line_of_the_run_script_is_blank_a_comment_a_continuation_or_a_known_statement():
    depth = 0
    for number, line in enumerate(RUN.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if depth > 0 and not STATEMENT.match(stripped):
            pass  # inside a block: only statements and braces are expected, and the check below still bans a bare file name
        assert not re.fullmatch(r"[A-Za-z0-9_.\-]+\.(ps1|py|exe)\b.*", stripped), f"line {number} starts with a bare file name, which PowerShell would run as a command: {stripped[:40]}"
        assert STATEMENT.match(stripped), f"line {number} is not a comment or a known statement: {stripped[:60]}"
        depth += stripped.count("{") - stripped.count("}")


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="no PowerShell is installed here")
@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_the_powershell_parser_accepts_the_script_and_finds_no_stray_command(path):
    exe = shutil.which("pwsh") or shutil.which("powershell")
    command = (
        "$errs = $null; $tokens = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$tokens, [ref]$errs); "
        "if ($errs.Count -gt 0) { $errs | ForEach-Object { Write-Output ('PARSE-ERROR ' + $_.Message) }; exit 1 }; "
        "$ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { Write-Output ('COMMAND ' + $_.GetCommandName()) }"
    )
    result = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0 and "PARSE-ERROR" not in result.stdout, result.stdout[:300]
    commands = {line.split(" ", 1)[1] for line in result.stdout.splitlines() if line.startswith("COMMAND ")}
    allowed = {"Test-Path", "Write-Host", "Get-Content", "Set-Item", "Get-Item", "Read-Host", "Write-Output", "ConvertTo-SecureString", "Get-Command"}
    if path in (RUN, UP):  # the other script defines its own functions; only the start scripts are held to this short list
        stray = {c for c in commands if c and c not in allowed and not c.startswith((".", "&"))}
        assert not stray, f"{path.name} would run unexpected commands: {sorted(stray)}"


def test_the_run_script_defaults_to_port_8000_refuses_outside_the_synthetic_dev_demo_and_never_prints_a_value():
    text = RUN.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "param([int]$Port = 8000)" in code and "--host 127.0.0.1 --port $Port" in code
    assert 'Test-Path ".env"' in code  # the .env of the CURRENT folder
    for refusal in ("CAREFLOW_PROFILE is not demo", "CAREFLOW_DATA_POLICY is not synthetic_only", "CAREFLOW_ENV is not dev"):
        assert refusal in code
    for message in re.findall(r'Write-Host "([^"]*)"', code):
        assert not re.search(r"\$(value|env:|t(?![a-z])|line)", message), f"a message could print a value: {message[:80]}"
    for name in ("CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY", "CAREFLOW_DEMO_PHASE3", "CAREFLOW_DEMO_PHASE4", "CAREFLOW_RUN_WORKER"):
        assert f"$env:{name} =" in code
    assert r".\scripts\run_demo_backend.ps1" in text and r"cd E:\Claude\careflow-run" in text  # the documented way to start it


def test_demo_up_runs_the_steps_in_order_asks_once_runs_no_git_and_never_prints_a_value():
    text = UP.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert code.count("Read-Host") == 1  # the host is confirmed once; the smoke check reads the same typed line from the pipe
    assert r"$typed | & .\.venv\Scripts\python.exe .\scripts\demo_smoke.py" in code
    order = [code.index(marker) for marker in ("careflow.migrate --check", "careflow.migrate\n", "demo_smoke.py", "run_demo_backend.ps1 -Port")]
    assert order == sorted(order), "the steps must run in the order: migrate check, migrate, smoke, start"
    assert not re.search(r"(?<![A-Za-z-])git\b", code), "demo_up.ps1 must not run git"  # the folder is brought up to date by hand
    assert "if ($LASTEXITCODE -ne 0)" in code[code.index("demo_smoke.py") : code.index("run_demo_backend.ps1 -Port")]  # the backend starts only after the smoke check passed
    for refusal in ("CAREFLOW_PROFILE is not demo", "CAREFLOW_DATA_POLICY is not synthetic_only", "CAREFLOW_ENV is not dev"):
        assert refusal in code
    for message in re.findall(r'Write-Host "([^"]*)"', code):
        assert not re.search(r"\$(value|env:|t(?![a-z])|line|typed)", message), f"a message could print a value: {message[:80]}"
    assert r".\scripts\demo_up.ps1" in text and r"cd E:\Claude\careflow-run" in text
