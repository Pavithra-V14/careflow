# Starts the CareFlow backend (the API and the queue worker, in one process) against the DEV project in the DEMO profile.
#
# Run it from the folder that holds your .env (the run folder), exactly like this:
#
#     cd E:\Claude\careflow-run
#     .\scripts\run_demo_backend.ps1
#
# Why a script: the backend reads some names from the real process environment (the profile, the data policy, the demo switches, the feature flags), not only from .env. This script copies every
# NAME=value line of .env from the CURRENT folder into the environment of THIS window (values are never printed), then sets the demo names listed below, then starts the server on
# http://127.0.0.1:8000 (or the port given with -Port). Stop it with Ctrl+C.
#
# It REFUSES to start unless the environment is the synthetic dev demo: CAREFLOW_PROFILE (if set) must be demo, CAREFLOW_DATA_POLICY (if set) must be synthetic_only and CAREFLOW_ENV (if set) must be dev.
# It prints names only, never a value. The account origin of a patient is derived by the backend from the profile (demo means test), so the profile MUST come through this script.
#
# Names it sets: CAREFLOW_PROFILE, CAREFLOW_DATA_POLICY, CAREFLOW_DEMO_PHASE3, CAREFLOW_DEMO_PHASE4, CAREFLOW_RUN_WORKER and (only when unset) CAREFLOW_CLINIC_TIME_ZONE.
# Names that must already be in .env: DATABASE_URL_APP, CAREFLOW_WORKER_ACCOUNT_ID, SUPABASE_JWKS_URL, CAREFLOW_ENC_SECRET_V1, CAREFLOW_HMAC_SECRET_V1.
# Switches you may add to .env (exactly on, restart after a change): see docs/demo-runbook.md sections 14 and 15.
param([int]$Port = 8000)  # another port if 8000 is taken (the frontend dev proxy expects 8000), for example: -Port 8010
$ErrorActionPreference = "Stop"
if (-not (Test-Path ".env")) { Write-Host "There is no .env in this folder ($PWD). Run this from the run folder (cd E:\Claude\careflow-run) and copy .env.example to .env first (see docs/first-run-guide.md)."; exit 2 }
if (-not (Test-Path ".\.venv\Scripts\python.exe")) { Write-Host "There is no .venv in this folder ($PWD). Run this from the run folder (see docs/first-run-guide.md)."; exit 2 }
foreach ($line in Get-Content ".env") {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    $i = $t.IndexOf("=")
    if ($i -lt 1) { continue }
    $name = $t.Substring(0, $i).Trim()
    if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }
    $value = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
    Set-Item -Path ("Env:" + $name) -Value $value
}
# refuse anything that is not the synthetic dev demo (names only; a value is never printed)
if ($env:CAREFLOW_PROFILE -and $env:CAREFLOW_PROFILE.Trim().ToLower() -ne "demo") { Write-Host "Refusing to start: CAREFLOW_PROFILE is not demo. This script is for the synthetic dev demo only."; exit 2 }
if ($env:CAREFLOW_DATA_POLICY -and $env:CAREFLOW_DATA_POLICY.Trim() -ne "synthetic_only") { Write-Host "Refusing to start: CAREFLOW_DATA_POLICY is not synthetic_only. This script is for the synthetic dev demo only."; exit 2 }
if ($env:CAREFLOW_ENV -and $env:CAREFLOW_ENV.Trim().ToLower() -ne "dev") { Write-Host "Refusing to start: CAREFLOW_ENV is not dev. This script is for the dev project only."; exit 2 }
# the demo names (synthetic data only)
$env:CAREFLOW_PROFILE = "demo"
$env:CAREFLOW_DATA_POLICY = "synthetic_only"
$env:CAREFLOW_DEMO_PHASE3 = "1"
$env:CAREFLOW_DEMO_PHASE4 = "1"
$env:CAREFLOW_RUN_WORKER = "true"
if (-not $env:CAREFLOW_CLINIC_TIME_ZONE) { $env:CAREFLOW_CLINIC_TIME_ZONE = "Asia/Calcutta" }
foreach ($required in @("DATABASE_URL_APP", "CAREFLOW_WORKER_ACCOUNT_ID", "SUPABASE_JWKS_URL", "CAREFLOW_ENC_SECRET_V1", "CAREFLOW_HMAC_SECRET_V1")) {
    if (-not (Get-Item -Path ("Env:" + $required) -ErrorAction SilentlyContinue)) { Write-Host "Missing in .env: $required (see docs/first-run-guide.md, section 3)"; exit 2 }
}
Write-Host "Starting the backend on http://127.0.0.1:$Port (demo profile, synthetic data only). Press Ctrl+C to stop."
& .\.venv\Scripts\python.exe -m uvicorn careflow.app:app --host 127.0.0.1 --port $Port
