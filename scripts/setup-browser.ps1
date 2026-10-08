# Alan - build the browser-use sidecar environment
# ---------------------------------------------------------------------------
# Creates env\browser\python (an isolated copy of Alan's portable Python) and
# installs browser-use into it. Idempotent: safe to re-run.
#
# WHY A SEPARATE ENVIRONMENT
# browser-use pins its dependencies with `==`, and those pins conflict with
# Alan's main environment (openai 3.24 vs 2.x, rich 15.0 vs 14.3, requests
# 2.34 vs 2.33). Sharing one environment would downgrade the provider layer and
# the CLI to satisfy a capability that is off by default. So the conflict is
# resolved by isolation, not by negotiation (ADR-003).
#
# NO BROWSER IS DOWNLOADED. Playwright normally fetches its own Chromium
# (~150 MB), which would break "one folder, zero host installs". Instead the
# sidecar drives the Chrome already installed on the host over CDP. If Chrome
# is missing, Edge is used -- both ship with Windows.
#
# Usage:  scripts\setup-browser.ps1
$ErrorActionPreference = 'Stop'

$root  = Split-Path -Parent $PSScriptRoot
$srcPy = Join-Path $root 'env\python'
$dstPy = Join-Path $root 'env\browser\python'
$py    = Join-Path $dstPy 'python.exe'

if (-not (Test-Path (Join-Path $srcPy 'python.exe'))) {
    Write-Error "Base portable Python not found at $srcPy."
    exit 1
}

if (-not (Test-Path $py)) {
    Write-Host "Creating isolated sidecar Python (copy of base env) ..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path $dstPy) | Out-Null
    Copy-Item -Recurse -Force $srcPy $dstPy
} else {
    Write-Host "Sidecar Python already present at $dstPy" -ForegroundColor Cyan
}

# Strip Alan's src hook from the copied ._pth, so the sidecar cannot import
# Alan's own modules and quietly couple the two environments.
#
# This runs unconditionally, not only on a fresh copy. Doing it only inside the
# copy branch meant a re-run after a partial failure left the src line in place
# and reported success -- the sidecar could then resolve `alan` from Alan's own
# source tree.
#
# The read is materialised into a variable before writing. Piping Get-Content
# straight into Set-Content on the same path keeps the read handle open and
# fails with "used by another process" on Windows.
$pth = Join-Path $dstPy 'python312._pth'
if (Test-Path $pth) {
    $lines = @(Get-Content -Path $pth)
    $clean = @($lines | Where-Object { $_ -notmatch 'src$' })
    if ($clean.Count -ne $lines.Count) {
        Set-Content -Path $pth -Value $clean
        Write-Host "Removed Alan's src hook from the sidecar ._pth" -ForegroundColor Cyan
    }
    if ($clean -match 'src$') {
        Write-Error "The sidecar ._pth still references Alan's src. Isolation is broken."
        exit 1
    }
}

$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
# Keep Playwright from fetching its own Chromium; the sidecar attaches to the
# Chrome/Edge already installed via CDP.
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'

# From here on, failures are judged by $LASTEXITCODE, not by whether a native
# command wrote to stderr. Under 'Stop', pip's routine
# "WARNING: the script wheel.exe is installed ... which is not on PATH"
# becomes a terminating error and kills a run that actually succeeded. That
# warning is expected here: the sidecar env is deliberately not on PATH.
$ErrorActionPreference = 'Continue'

Write-Host "Upgrading pip/setuptools/wheel ..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip setuptools wheel 2>&1 | Select-Object -Last 2
if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed."; exit 1 }

Write-Host "Installing browser-use (isolated; several minutes) ..." -ForegroundColor Cyan
& $py -m pip install browser-use 2>&1 | Select-Object -Last 12
if ($LASTEXITCODE -ne 0) { Write-Error "browser-use install failed."; exit 1 }

# No `playwright install` step: browser-use 0.13 dropped Playwright and drives
# Chrome over CDP directly (via cdp-use), so there is no driver to fetch and no
# browser binary to download. Installing one anyway would add ~150 MB and a
# second, redundant control path over the same browser.

Write-Host ""
Write-Host "Verifying the sidecar imports ..." -ForegroundColor Cyan
& $py -c "import browser_use, importlib.metadata as m; print('browser-use', m.version('browser-use'))" 2>&1 |
    Select-Object -Last 3
if ($LASTEXITCODE -ne 0) {
    Write-Error "browser-use is installed but does not import. The sidecar cannot run."
    exit 1
}

Write-Host ""
Write-Host "Sidecar environment ready." -ForegroundColor Green
Write-Host "Start it with: scripts\browser-serve.ps1" -ForegroundColor Green
Write-Host "Then set [tools] browser = true in config\settings.toml." -ForegroundColor Green
