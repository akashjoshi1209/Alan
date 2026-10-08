# Alan's offline verification. No network, no model server, no browser.
#
#   scripts\selftest.ps1          unit tests + demos (the default)
#   scripts\selftest.ps1 -Unit    unit tests only
#   scripts\selftest.ps1 -Demos   demos only
#
# Exits non-zero if anything fails, so it can gate a release.

[CmdletBinding()]
param(
    [switch] $Unit,
    [switch] $Demos
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$pyDir = Join-Path $root 'env\python'
$py = Join-Path $pyDir 'python.exe'

if (-not (Test-Path $py)) {
    Write-Host "No bundled interpreter at $py" -ForegroundColor Red
    Write-Host "Run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

# The bundled environment is self-contained and is not on the host PATH; these
# are the same variables run.ps1 sets, so a test run matches a real run.
$env:ALAN_HOME           = $root
$env:PYTHONUTF8          = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH          = Join-Path $root 'src'
# Probe results are cached between processes. Tests stub transports, so a probe
# recorded by an earlier run would answer for a stub and make results depend on
# execution order. Every test here wants the real backend, every time.
$env:ALAN_PROBE_CACHE    = '0'
$env:PATH                = "$pyDir;$(Join-Path $pyDir 'Scripts');$env:PATH"

if (-not $Unit -and -not $Demos) { $Unit = $true; $Demos = $true }

$failed = @()

Push-Location $root
try {
    if ($Unit) {
        Write-Host ''
        Write-Host '=== unit tests (offline) ===' -ForegroundColor Cyan
        & $py -m unittest discover -s tests 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { $failed += 'unit tests' }

        # A second run: the memory store is written from several threads and a
        # pass that only sometimes fails is worse than a failure.
        Write-Host ''
        Write-Host '=== unit tests, second run (concurrency check) ===' -ForegroundColor Cyan
        & $py -m unittest discover -s tests 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { $failed += 'unit tests (second run)' }
    }

    if ($Demos) {
        Write-Host ''
        Write-Host '=== demonstrations ===' -ForegroundColor Cyan
        & $py (Join-Path $root 'demos\demo_all.py') 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { $failed += 'demos' }
    }

    Write-Host ''
    if ($failed.Count -eq 0) {
        Write-Host 'SELFTEST PASSED' -ForegroundColor Green
        exit 0
    }
    Write-Host ("SELFTEST FAILED: " + ($failed -join ', ')) -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
