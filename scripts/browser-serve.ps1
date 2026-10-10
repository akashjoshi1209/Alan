# Alan - browser sidecar
# ---------------------------------------------------------------------------
# Runs the browser-use sidecar on 127.0.0.1:8022 from an ISOLATED environment
# (env\browser\python), so browser-use's `==` pins cannot conflict with Alan's
# main environment (ADR-003).
#
# No browser binary is downloaded. browser-use 0.13 drives the Chrome already
# installed on the host, or Edge, over CDP. Headless by default; set
# ALAN_BROWSER_HEADLESS=0 to watch it work.
#
# Usage:
#   scripts\browser-serve.ps1          -> start (no-op if already running)
#   scripts\browser-serve.ps1 -Stop    -> stop
#   scripts\browser-serve.ps1 -Status  -> report health
param(
    [switch]$Stop,
    [switch]$Status
)

# 'Continue' rather than 'Stop': a sidecar that fails to start must report
# which step failed, not abort on the first non-terminating error and leave the
# user with no idea what happened.
$ErrorActionPreference = 'Continue'

$root    = Split-Path -Parent $PSScriptRoot          # <Alan root>
$pyDir   = Join-Path $root 'env\browser\python'
$py      = Join-Path $pyDir 'python.exe'
$server  = Join-Path $root 'src\browser_sidecar.py'
$port    = 8022
$health  = "http://127.0.0.1:$port/health"
$runRoot = Join-Path $root 'data\browser'
$logDir  = Join-Path $runRoot 'logs'
$pidFile = Join-Path $runRoot 'sidecar.pid'
$outLog  = Join-Path $logDir 'sidecar.out.log'
$errLog  = Join-Path $logDir 'sidecar.err.log'

function Test-Sidecar {
    try {
        Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Get-HealthBody {
    try {
        return (Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 3).Content
    } catch { return $null }
}

if ($Status) {
    if (Test-Sidecar) {
        Write-Host "browser sidecar: UP   http://127.0.0.1:$port" -ForegroundColor Green
        $body = Get-HealthBody
        if ($body) { Write-Host "  $body" -ForegroundColor DarkGray }
        exit 0
    }
    Write-Host "browser sidecar: DOWN" -ForegroundColor Yellow
    exit 1
}

if ($Stop) {
    # Ask the sidecar to close its browser first. `Stop-Process -Force` calls
    # TerminateProcess, which on Windows runs no handler and no `finally`, so
    # the Chrome the sidecar started survived the stop and kept running with
    # nothing left to stop it -- that is how 125 orphan processes accumulated.
    $stopped = $false
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/shutdown" -Method Post `
              -TimeoutSec 8 -ErrorAction Stop
        if ($r.stopping) { $stopped = $true }
    } catch { }
    if ($stopped) {
        # Give it a moment to close the browser and exit on its own.
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Milliseconds 250
            $still = Get-Process -Id $r.pid -ErrorAction SilentlyContinue
            if (-not $still) { break }
        }
    }
    if (Test-Path $pidFile) {
        $sidecarPid = 0
        [void][int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$sidecarPid)
        if ($sidecarPid -gt 0) {
            # Still alive after the graceful request: force it, and say so,
            # because a forced stop may leave a browser behind for the next
            # start to reap.
            $alive = Get-Process -Id $sidecarPid -ErrorAction SilentlyContinue
            if ($alive) {
                Write-Host "sidecar did not exit gracefully; forcing." -ForegroundColor Yellow
                try { Stop-Process -Id $sidecarPid -Force -ErrorAction Stop; $stopped = $true } catch { }
            } else { $stopped = $true }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
    # Fallback: stop whatever owns the port.
    try {
        $owner = (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop | Select-Object -First 1).OwningProcess
        if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; $stopped = $true }
    } catch { }
    if ($stopped) { Write-Host "browser sidecar stopped." -ForegroundColor Cyan }
    else { Write-Host "browser sidecar was not running." -ForegroundColor Yellow }
    exit 0
}

if (-not (Test-Path $py)) {
    Write-Error "Sidecar Python not found at $py. Run scripts\setup-browser.ps1 first."
    exit 1
}

if (-not (Test-Path $server)) {
    Write-Error "Sidecar not found at $server."
    exit 1
}

if (Test-Sidecar) {
    Write-Host "browser sidecar already up on port $port." -ForegroundColor Cyan
    exit 0
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# --- Sidecar configuration -------------------------------------------------------
# The sidecar must not be able to reach Alan's package tree. Its ._pth already
# has `..\..\src` stripped, but clearing PYTHONPATH here means the launcher is
# safe even if someone runs it with the variable already set in their shell --
# which is exactly what a developer testing from source will have done.
$env:PYTHONPATH          = ''

# Keep bytecode caches out of the source tree: compile into host temp.
$env:PYTHONPYCACHEPREFIX = (Join-Path $env:TEMP 'alan-browser-pyc')

# Loopback only. The sidecar drives a real browser and can reach authenticated
# sessions; it has no business listening on a routable interface.
$env:ALAN_BROWSER_PORT   = "$port"

Write-Host "Starting browser sidecar (port $port) ..." -ForegroundColor Cyan

# NOTE: Start-Process creates the child with handle inheritance on (it has to,
# to hand over the redirection handles). The sidecar therefore also holds a
# duplicate of THIS script's own stdout handle for as long as it runs. Two
# consequences, both expected and neither a fault in this script:
#
#   * Redirecting this script to a file, e.g. `browser-serve.ps1 > out.txt`,
#     leaves out.txt locked until the sidecar exits.
#   * Piping this script's output (`| Tee-Object`, `| Select-Object`) can leave
#     the reader waiting on a pipe that the sidecar still holds open.
#
# This script itself completes and returns control promptly; the sidecar is
# verified to outlive it.
$proc = Start-Process -FilePath $py -ArgumentList $server `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog

$proc.Id | Out-File -FilePath $pidFile -Encoding ascii

# Unlike the cognee sidecar this one is ready almost immediately: there is no
# model download and no database migration, and no browser is launched until a
# tool actually asks for one. So a short wait is the right shape here -- and a
# long one would only delay the report of a genuine failure.
$timeoutSec = 15
$ok = $false
$exited = $false

for ($i = 0; $i -lt $timeoutSec; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Sidecar) { $ok = $true; break }
    if ($proc.HasExited) { $exited = $true; break }
}

if ($ok) {
    Write-Host "browser sidecar ready: http://127.0.0.1:$port" -ForegroundColor Green
    $body = Get-HealthBody
    if ($body) { Write-Host "  $body" -ForegroundColor DarkGray }
    # `session: not started` above is correct and expected: nothing launches a
    # browser until a tool is called and the permission gate approves it.
    Write-Host ""
    Write-Host "[tools] browser = true is already the default in config\settings.toml." -ForegroundColor DarkGray
    exit 0
}

if ($exited) {
    Write-Error "browser sidecar exited during startup (see $errLog)"
    if (Test-Path $errLog) { Get-Content $errLog -Tail 30 | ForEach-Object { Write-Host $_ } }
    exit 1
}

# Still running, still not answering /health. Neither claim success nor failure.
Write-Warning "browser sidecar is running but not healthy after ${timeoutSec}s (pid $($proc.Id))."
Write-Warning "Check $errLog, then re-run with -Status."
exit 2
