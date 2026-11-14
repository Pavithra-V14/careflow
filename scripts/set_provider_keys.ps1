# Set up the three hosted-provider keys for the SYNTHETIC DEMO, without anyone ever seeing a key.   (owner request 2026-11-14; docs/design/real-model-demo.md)
#
# Run it from the folder that holds your .env (the repository, or E:\Claude\careflow-run):
#
#     powershell -ExecutionPolicy Bypass -File .\scripts\set_provider_keys.ps1
#
# What it does, in order:
#   1. REFUSES to run if the .env is tracked by git (or is inside a git folder and not ignored), or if the .env does not say CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only
#      (a clinical or unset profile is refused). Nothing is written in that case.
#   2. For each provider it prints ONE plain line saying where to get the key, then asks you to paste it. What you paste is HIDDEN (nothing is shown as you type or paste), is held in memory only
#      long enough to be written, and is written to ONE place: that provider's variable in the .env in this folder. Only that line is replaced; every other line is left exactly as it was.
#   3. Asks for each role's daily spend ceiling and for the per-run spend cap (the app refuses to use a provider until they are set).
#   4. Runs scripts\provider_live_check.py as the last step and shows ONLY status (PASS or FAIL), time and cost. Any key is masked in what it shows. For a provider whose lines all PASS it records
#      today's date as its VERIFIED setting (the app builds a provider only after that date is recorded).
#
# It never prints, logs, copies or stores a key anywhere else: no transcript, no history entry, no temp file, no other file. The three keys never leave your machine except in the one request
# each provider's own check sends to that provider. The input is synthetic only (a dummy letter, a made-up sentence).

$ErrorActionPreference = 'Stop'

$Providers = @(
    @{ Name = 'Gemini';     Key = 'gemini'; Var = 'GEMINI_API_KEY';     Verified = 'CAREFLOW_GEMINI_VERIFIED';     Ceiling = 'CAREFLOW_SPEND_CEILING_VLM_USD';         CeilingDefault = '0'
       Where = 'Open aistudio.google.com, sign in, make a NEW project used only for CareFlow dev, click Get API key, create a key in that project, and leave billing OFF.' },
    @{ Name = 'OpenRouter'; Key = 'jev';    Var = 'OPENROUTER_API_KEY'; Verified = 'CAREFLOW_OPENROUTER_VERIFIED'; Ceiling = 'CAREFLOW_SPEND_CEILING_DECISION_USD';    CeilingDefault = '1'
       Where = 'Open openrouter.ai, sign in, go to Settings then Keys, create a key named careflow-dev with a credit limit of a few dollars, and add only that much credit.' },
    @{ Name = 'Sarvam';     Key = 'sarvam'; Var = 'SARVAM_API_KEY';     Verified = 'CAREFLOW_SARVAM_VERIFIED';     Ceiling = 'CAREFLOW_SPEND_CEILING_TRANSLATION_USD'; CeilingDefault = '1'
       Where = 'Open dashboard.sarvam.ai, sign in, go to API Keys, create a key named careflow-dev, use the free credits and add no card yet.' }
)
$CapVar = 'CAREFLOW_SPEND_CAP_PER_RUN_USD'

function Get-EnvVariable([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match ('^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*=\s*(.*?)\s*$')) {
            return $Matches[1].Trim('"').Trim("'")
        }
    }
    return $null
}

# Replaces (or appends) exactly one variable in the .env. Every other line is written back unchanged. This is the ONLY function in this script that writes a file.
function Set-EnvVariable([string]$Path, [string]$Name, [string]$Value) {
    if ($Value -match '[\r\n]') { throw 'a value cannot contain a line break' }
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $Path) { foreach ($l in [System.IO.File]::ReadAllLines($Path)) { $lines.Add($l) } }
    $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*='
    $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            if (-not $done) { $lines[$i] = "$Name=$Value"; $done = $true } else { $lines.RemoveAt($i); $i-- }
        }
    }
    if (-not $done) { $lines.Add("$Name=$Value") }
    [System.IO.File]::WriteAllLines($Path, $lines.ToArray(), (New-Object System.Text.UTF8Encoding($false)))
}

function Test-KeyShape([string]$Value) { return ($Value -match '^[A-Za-z0-9._:\-]{8,300}$') }

function Test-AmountShape([string]$Value, [bool]$AllowZero) {
    $d = [decimal]0
    if (-not [decimal]::TryParse($Value, [System.Globalization.NumberStyles]::Number, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$d)) { return $false }
    if ($AllowZero) { return ($d -ge 0 -and $d -le 1000) }
    return ($d -gt 0 -and $d -le 1000)
}

function Mask-Line([string]$Line, [string[]]$Secrets) {
    foreach ($s in $Secrets) { if ($s -and $s.Length -ge 8) { $Line = $Line.Replace($s, '***') } }
    return $Line
}

# When this file is dot-sourced (the test does that to exercise the functions above) stop here and run nothing.
if ($MyInvocation.InvocationName -eq '.') { return }

$envPath = Join-Path (Get-Location).Path '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    Write-Host 'Stopped: there is no .env in this folder. Open PowerShell in the folder that holds your .env and run this again. Nothing was written.'
    exit 2
}

# ---- refuse if the .env could end up in git ---------------------------------------------------------------------------------------------------------------------------------------
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    $ErrorActionPreference = 'Continue'
    & git rev-parse --is-inside-work-tree *> $null
    $inside = ($LASTEXITCODE -eq 0)
    if ($inside) {
        & git ls-files --error-unmatch -- .env *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host 'Stopped: git TRACKS this .env, so a key written to it could be committed. Remove it from git first (the project owner knows how). Nothing was written.'
            exit 2
        }
        & git check-ignore -q -- .env *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'Stopped: this .env is inside a git folder and is NOT ignored by git, so a key could be committed by accident. Add .env to .gitignore first. Nothing was written.'
            exit 2
        }
    }
    $ErrorActionPreference = 'Stop'
}

# ---- refuse unless the profile is the synthetic demo ------------------------------------------------------------------------------------------------------------------------------------
$profileName = Get-EnvVariable $envPath 'CAREFLOW_PROFILE'
$policy = Get-EnvVariable $envPath 'CAREFLOW_DATA_POLICY'
if ($profileName -ne 'demo' -or $policy -ne 'synthetic_only') {
    Write-Host 'Stopped: these hosted providers are for the SYNTHETIC DEMO only. Your .env must say CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only (a clinical or missing profile is refused). Nothing was written.'
    exit 2
}

Write-Host ''
Write-Host 'Nothing you paste is shown or saved anywhere except your .env. Press Enter on a key to keep the one already saved.'
Write-Host ''

# ---- keys and daily ceilings ------------------------------------------------------------------------------------------------------------------------------------------------------------
foreach ($p in $Providers) {
    Write-Host ($p.Name + ': ' + $p.Where)
    $secure = Read-Host ('Paste the ' + $p.Name + ' key (hidden)') -AsSecureString
    if ($secure.Length -eq 0) {
        if (Get-EnvVariable $envPath $p.Var) { Write-Host '  kept the saved key.' } else { Write-Host '  no key saved for this provider; it will be skipped.' }
    } else {
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if (Test-KeyShape $plain) {
                Set-EnvVariable $envPath $p.Var $plain
                Write-Host '  saved to .env.'
            } else {
                Write-Host '  that does not look like a key (letters, digits and - _ . : only, at least 8). Nothing was saved for this provider.'
            }
        } finally {
            $plain = $null
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            $secure.Dispose()
        }
    }
    $current = Get-EnvVariable $envPath $p.Ceiling
    $shown = if ($current) { $current } else { $p.CeilingDefault }
    $answer = Read-Host ('  Most ' + $p.Name + ' may spend in one DAY, in US dollars [' + $shown + ']')
    if (-not $answer) { $answer = $shown }
    if (Test-AmountShape $answer $true) { Set-EnvVariable $envPath $p.Ceiling $answer } else { Write-Host '  that is not an amount; the daily ceiling was left as it was.' }
    Write-Host ''
}

# ---- the per-run cap ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
$capNow = Get-EnvVariable $envPath $CapVar
$capShown = if ($capNow) { $capNow } else { '0.10' }
$cap = Read-Host ('Most ONE job run may spend through the app, in US dollars, above zero [' + $capShown + ']')
if (-not $cap) { $cap = $capShown }
if (Test-AmountShape $cap $false) {
    Set-EnvVariable $envPath $CapVar $cap
} else {
    Write-Host 'That is not an amount above zero, so the per-run cap was NOT set and the app will refuse to use any provider until it is. Run this again.'
}

# ---- the live check, last --------------------------------------------------------------------------------------------------------------------------------------------------------------
Write-Host ''
if (-not (Test-Path -LiteralPath '.\scripts\provider_live_check.py')) {
    Write-Host 'Saved. The live check script is not in this folder (scripts\provider_live_check.py), so it was not run.'
    exit 0
}
if (Test-Path -LiteralPath '.\.venv\Scripts\python.exe') { $py = '.\.venv\Scripts\python.exe'; $pyArgs = @() }
elseif (Get-Command uv -ErrorAction SilentlyContinue) { $py = 'uv'; $pyArgs = @('run', 'python') }
else {
    Write-Host 'Saved. Python was not found, so the live check was not run.'
    exit 0
}
Write-Host 'Running the live check (one small request to each provider, synthetic input)...'
$secrets = @()
foreach ($p in $Providers) { $v = Get-EnvVariable $envPath $p.Var; if ($v) { $secrets += $v } }
$ErrorActionPreference = 'Continue'
$raw = & $py @pyArgs 'scripts\provider_live_check.py' '--provider' 'all' '--yes-synthetic' 2>$null
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'

$block = $null
$passes = @{}
$fails = @{}
$shownAny = $false
foreach ($line in $raw) {
    $text = [string]$line
    if ($text -match '^--- (\w+)') { $block = $Matches[1]; Write-Host (Mask-Line $text $secrets); $shownAny = $true; continue }
    if ($text -match '^PASS ') { if ($block) { $passes[$block] = $true }; Write-Host (Mask-Line $text $secrets); $shownAny = $true; continue }
    if ($text -match '^FAIL ') { if ($block) { $fails[$block] = $true }; Write-Host (Mask-Line $text $secrets); $shownAny = $true; continue }
    if ($text -match '^(refusing|ALL EXPECTATIONS|EXPECTATIONS NOT MET)') { Write-Host (Mask-Line $text $secrets); $shownAny = $true }
}
if (-not $shownAny) { Write-Host ('The live check printed nothing I can show (exit code ' + $code + '). No key was shown. Check that the .env says CAREFLOW_PROFILE=demo.') }
$today = (Get-Date).ToString('yyyy-MM-dd')
foreach ($p in $Providers) {
    if ($passes[$p.Key] -and -not $fails[$p.Key]) {
        Set-EnvVariable $envPath $p.Verified $today
        Write-Host ($p.Name + ': every check passed, so ' + $p.Verified + ' is now ' + $today + ' in your .env.')
    } elseif ($fails[$p.Key]) {
        Write-Host ($p.Name + ': a check failed, so its VERIFIED date was NOT recorded. Send me the lines above.')
    }
}
Write-Host ''
Write-Host 'Done. Restart the backend so it picks up the new settings.'
exit $(if ($code -eq 0) { 0 } else { 1 })
