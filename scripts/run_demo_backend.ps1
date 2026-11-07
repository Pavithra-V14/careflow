# Starts the CareFlow backend (the API and the queue worker, in one process) against the DEV project in the DEMO profile.
#
#   cd E:\Claude\careflow-run
#   .\scripts\run_demo_backend.ps1
#
# Why a script: the backend reads some names from the real process environment (the profile, the data policy, the demo switches, the feature flags), not only from .env. This script copies every NAME=value
# line of .env into the environment of THIS window (values are never printed), then sets the demo switches, then starts the server on http://127.0.0.1:8000. Stop it with Ctrl+C.
$ErrorActionPreference = "Stop"
if (-not (Test-Path ".env")) { Write-Host "There is no .env in this folder. Copy .env.example to .env and fill it in first (see docs/first-run-guide.md)."; exit 2 }
foreach ($line in Get-Content ".env") {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    $i = $t.IndexOf("=")
    if ($i -lt 1) { continue }
    $name = $t.Substring(0, $i).Trim()
    $value = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
    Set-Item -Path ("Env:" + $name) -Value $value
}
# the demo switches (synthetic data only)
$env:CAREFLOW_PROFILE = "demo"
$env:CAREFLOW_DATA_POLICY = "synthetic_only"
$env:CAREFLOW_DEMO_PHASE3 = "1"
$env:CAREFLOW_DEMO_PHASE4 = "1"
$env:CAREFLOW_RUN_WORKER = "true"
if (-not $env:CAREFLOW_CLINIC_TIME_ZONE) { $env:CAREFLOW_CLINIC_TIME_ZONE = "Asia/Calcutta" }
foreach ($required in @("DATABASE_URL_APP", "CAREFLOW_WORKER_ACCOUNT_ID", "SUPABASE_JWKS_URL", "CAREFLOW_ENC_SECRET_V1", "CAREFLOW_HMAC_SECRET_V1")) {
    if (-not (Get-Item -Path ("Env:" + $required) -ErrorAction SilentlyContinue)) { Write-Host "Missing in .env: $required (see docs/first-run-guide.md, section 3)"; exit 2 }
}
Write-Host "Starting the backend on http://127.0.0.1:8000 (demo profile, synthetic data only). Press Ctrl+C to stop."
& .\.venv\Scripts\python.exe -m uvicorn careflow.app:app --host 127.0.0.1 --port 8000
