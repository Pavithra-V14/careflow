"""scripts/set_provider_keys.ps1: it never writes a key anywhere except the one variable in the .env of the current folder, and never prints one. Two halves: a STATIC half that reads the script (always runs) and a BEHAVIOURAL half that runs it with PowerShell when
PowerShell is installed (skipped otherwise): the writer function on a canary key, and the refusals (a clinical profile, a .env that git tracks). Synthetic canary strings only; no real key is ever used."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "set_provider_keys.ps1"
SOURCE = SCRIPT.read_text(encoding="utf-8")
CANARY = "CANARYKEY-0123456789-NOTREAL"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed here")


# ---- static: what the script can possibly do ----------------------------------------------------------------------------------------------------------------------------------------------------------


def code_lines() -> list[str]:
    return [line for line in SOURCE.splitlines() if not line.lstrip().startswith("#")]


JOINED = "\n".join(code_lines())  # the script without its comment lines


def test_the_only_thing_that_writes_a_file_is_the_one_env_writer():
    code = "\n".join(code_lines())
    writers = re.findall(r"WriteAllLines|WriteAllText|Out-File|Set-Content|Add-Content|Tee-Object|Start-Transcript|\bsc\b|>>|\s>\s|New-Item|Export-Clixml|Copy-Item", code)
    assert writers == ["WriteAllLines"], writers  # exactly one write, inside Set-EnvVariable
    body = code.split("function Set-EnvVariable", 1)[1].split("\nfunction ", 1)[0]
    assert "WriteAllLines" in body and "$Path" in body


def test_the_env_writer_is_only_ever_called_on_the_env_path():
    calls = re.findall(r"Set-EnvVariable\s+(\S+)", "\n".join(code_lines()))
    assert calls and all(c in ("$envPath", "[string]$Path,") for c in calls), calls


def test_a_key_is_read_hidden_and_never_echoed():
    code = "\n".join(code_lines())
    assert "-AsSecureString" in code
    for sink in ("Write-Host", "Write-Output", "Write-Verbose", "Write-Debug", "Write-Warning", "Write-Error", "Out-Host", "Out-String", "Write-Information"):
        for line in code.splitlines():
            if sink in line:
                assert "$plain" not in line and "$secure" not in line and "$Value" not in line, line
    assert "Start-Transcript" not in code and "Set-PSDebug" not in code and "Add-History" not in code


def test_it_refuses_a_tracked_env_and_a_non_demo_profile_before_it_asks_for_anything():
    code = JOINED
    first_prompt = code.index("Read-Host")
    assert code.index("ls-files --error-unmatch") < first_prompt and code.index("check-ignore") < first_prompt
    assert code.index("$profileName -ne 'demo'") < first_prompt and "$policy -ne 'synthetic_only'" in code


def test_the_live_check_runs_last_and_only_status_lines_are_shown_with_keys_masked():
    code = JOINED
    assert code.index("provider_live_check.py") > code.index("Set-EnvVariable $envPath $CapVar $cap")
    assert "Mask-Line" in code and "'--yes-synthetic'" in code
    shown = re.findall(r"Write-Host \(Mask-Line", SOURCE)
    assert len(shown) >= 4  # every line copied from the check goes through the mask
    assert "info:" not in SOURCE.split("foreach ($line in $raw)", 1)[1].split("$today", 1)[0]  # the check's free-text info lines are not shown


# ---- behavioural, when PowerShell is available ------------------------------------------------------------------------------------------------------------------------------------------------------


def run_ps(command: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command], cwd=cwd, capture_output=True, text=True, timeout=120, check=False)  # type: ignore[list-item]


def run_script(cwd: Path) -> subprocess.CompletedProcess:
    """Runs the whole script as a file, so its own exit code comes back."""
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)], cwd=cwd, capture_output=True, text=True, timeout=120, check=False)  # type: ignore[list-item]


def files_with(root: Path, needle: str) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and needle.encode() in p.read_bytes())


@needs_powershell
def test_the_writer_replaces_only_its_own_line_and_the_key_appears_in_no_other_file_and_no_output(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# a comment\nCAREFLOW_PROFILE=demo\nGEMINI_API_KEY=old-value-1234\nOTHER=keep me\nCAREFLOW_DATA_POLICY=synthetic_only\n", encoding="utf-8")
    before = env.read_text(encoding="utf-8").splitlines()
    done = run_ps(f". '{SCRIPT}'; Set-EnvVariable '{env}' 'GEMINI_API_KEY' '{CANARY}'; Set-EnvVariable '{env}' 'NEW_ONE' 'x1'", tmp_path)
    assert done.returncode == 0, done.stderr
    after = env.read_text(encoding="utf-8").splitlines()
    assert after == [before[0], before[1], f"GEMINI_API_KEY={CANARY}", before[3], before[4], "NEW_ONE=x1"]  # one line replaced, one appended, nothing else touched
    assert CANARY not in done.stdout and CANARY not in done.stderr
    assert files_with(tmp_path, CANARY) == [".env"]  # no other file anywhere holds the key


@needs_powershell
def test_a_clinical_or_missing_profile_is_refused_and_nothing_is_written(tmp_path):
    for content in ("CAREFLOW_PROFILE=clinical\nCAREFLOW_DATA_POLICY=synthetic_only\n", "CAREFLOW_DATA_POLICY=synthetic_only\n", "CAREFLOW_PROFILE=demo\n"):
        (tmp_path / ".env").write_text(content, encoding="utf-8")
        done = run_script(tmp_path)
        assert done.returncode == 2 and "SYNTHETIC DEMO only" in done.stdout, (content, done.stdout, done.stderr)
        assert (tmp_path / ".env").read_text(encoding="utf-8") == content
        assert [p.name for p in tmp_path.iterdir()] == [".env"]


@needs_powershell
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed here")
def test_a_env_that_git_tracks_is_refused(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    content = "CAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=synthetic_only\n"
    (tmp_path / ".env").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-f", ".env"], cwd=tmp_path, check=True)
    done = run_script(tmp_path)
    assert done.returncode == 2 and "TRACKS" in done.stdout, (done.stdout, done.stderr)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == content


@needs_powershell
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed here")
def test_a_env_inside_a_git_folder_that_is_not_ignored_is_refused(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    content = "CAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=synthetic_only\n"
    (tmp_path / ".env").write_text(content, encoding="utf-8")
    done = run_script(tmp_path)
    assert done.returncode == 2 and "NOT ignored" in done.stdout, (done.stdout, done.stderr)
