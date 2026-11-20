# One command to bring the dev demo up: migrate, check, and (only if every check passes) start the backend. DEMO ONLY: never use this outside the synthetic dev demo.
#
#     cd E:\Claude\careflow-run
#     .\scripts\demo_up.ps1
#
# It does NOT run git: bring the folder up to date yourself first (docs/demo-runbook.md section 0). In order it: reads the .env of the CURRENT folder (names only are ever printed), refuses unless the
# environment is the synthetic dev demo, asks you ONCE to type the project host, runs the migration check and the migrations, runs the smoke check (scripts/demo_smoke.py: PASS or FAIL lines and a "what to do"
# line under every FAIL), and only if everything passed starts the demo backend on 127.0.0.1:8000 by calling scripts\run_demo_backend.ps1. If a demo backend is already running and ready on the port it says so and
# does not start a second one. It never prints a secret value.
#
# Names you may need to know: the dev clinic id is the -ClinicId parameter (default: the dev clinic written in the demo runbook); -Port is 8000 unless you must change it (the frontend proxy expects 8000).
param([int]$Port = 8000, [string]$ClinicId = "b230e20e-655f-410d-8398-d2eadcb44dd2")
$ErrorActionPreference = "Stop"
if (-not (Test-Path ".env")) { Write-Host "There is no .env in this folder ($PWD). Run this from the run folder (cd E:\Claude\careflow-run)."; exit 2 }
if (-not (Test-Path ".\.venv\Scripts\python.exe")) { Write-Host "There is no .venv in this folder ($PWD). Run this from the run folder (see docs/first-run-guide.md)."; exit 2 }
if (-not (Test-Path ".\scripts\run_demo_backend.ps1")) { Write-Host "scripts\run_demo_backend.ps1 is missing: bring the folder up to date first (demo runbook section 0)."; exit 2 }
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
# refuse anything that is not the synthetic dev demo (names only)
if ($env:CAREFLOW_PROFILE -and $env:CAREFLOW_PROFILE.Trim().ToLower() -ne "demo") { Write-Host "Refusing: CAREFLOW_PROFILE is not demo. This script is for the synthetic dev demo only."; exit 2 }
if ($env:CAREFLOW_DATA_POLICY -and $env:CAREFLOW_DATA_POLICY.Trim() -ne "synthetic_only") { Write-Host "Refusing: CAREFLOW_DATA_POLICY is not synthetic_only. This script is for the synthetic dev demo only."; exit 2 }
if ($env:CAREFLOW_ENV -and $env:CAREFLOW_ENV.Trim().ToLower() -ne "dev") { Write-Host "Refusing: CAREFLOW_ENV is not dev. This script is for the dev project only."; exit 2 }
$env:CAREFLOW_PROFILE = "demo"
$env:CAREFLOW_DATA_POLICY = "synthetic_only"
$env:CAREFLOW_ENV = "dev"
foreach ($required in @("SUPABASE_URL", "DATABASE_URL_MIGRATOR", "DATABASE_URL_APP", "CAREFLOW_DEV_PROJECT_REF")) {
    if (-not (Get-Item -Path ("Env:" + $required) -ErrorAction SilentlyContinue)) { Write-Host "Missing in .env: $required (see docs/first-run-guide.md, section 3)"; exit 2 }
}
# the one host confirmation (the smoke check reads the same typed line, so you are asked only once)
$hostName = ([uri]$env:SUPABASE_URL).Host
Write-Host "target project host: $hostName"
$typed = Read-Host "Type that host exactly to confirm (anything else stops here)"
if ($typed.Trim() -ne $hostName) { Write-Host "Refusing: that is not the project host shown. Nothing was done."; exit 1 }
Write-Host ""
Write-Host "1. Migrations (check, then apply)"
& .\.venv\Scripts\python.exe -m careflow.migrate --check
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL migrations: the check stopped. What to do: read the message above (it names the migration); do not continue until it is fixed."; exit 1 }
& .\.venv\Scripts\python.exe -m careflow.migrate
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL migrations: applying stopped. What to do: read the message above (it names the migration); do not continue until it is fixed."; exit 1 }
Write-Host ""
Write-Host "2. Smoke check"
$typed | & .\.venv\Scripts\python.exe .\scripts\demo_smoke.py --clinic-id $ClinicId --port $Port | ForEach-Object {
    if ($_ -match '^FAIL') { Write-Host $_ -ForegroundColor Red }
    elseif ($_ -match '^WARN') { Write-Host $_ -ForegroundColor Yellow }
    elseif ($_ -match '^SKIP') { Write-Host $_ -ForegroundColor DarkGray }
    elseif ($_ -match '^PASS') { Write-Host $_ -ForegroundColor Green }
    else { Write-Host $_ }
}
$smokeExit = $LASTEXITCODE
# only a FAIL stops the start (the smoke check exits 1 only for a FAIL): a WARN is shown in yellow and the backend still starts; a SKIP is shown in grey
if ($smokeExit -ne 0) { Write-Host ""; Write-Host "Not starting the backend: fix the FAIL lines above (each has a what-to-do line) and run this again."; exit 1 }
Write-Host ""
Write-Host "3. Backend"
$listening = $false
try { $probe = [System.Net.Sockets.TcpClient]::new(); $probe.Connect("127.0.0.1", $Port); $probe.Close(); $listening = $true } catch { $listening = $false }
if ($listening) { Write-Host "A demo backend is already running and ready on http://127.0.0.1:$Port, so no second one is started. Nothing else to do."; exit 0 }
Write-Host "Everything passed. Starting the demo backend (Ctrl+C stops it)."
& .\scripts\run_demo_backend.ps1 -Port $Port
