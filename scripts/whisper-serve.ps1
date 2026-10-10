# Alan - Whisper Flow speech-to-text sidecar
# ---------------------------------------------------------------------------
# Runs the pinned whisper-flow FastAPI server (vendor\whisper-flow) in its own
# environment (env\whisper\python), on 127.0.0.1:8181. Clients stream 16 kHz
# mono int16 PCM to /ws and get transcripts back; Alan's `voice` command is the
# push-to-talk client. Nothing leaves the machine: the model is the bundled
# Whisper tiny.en, loaded locally.
#
# Ports: 8181 here, cognee 8012, browser 8022, Ollama 11435 -- no overlap.
#
# Usage:
#   scripts\whisper-serve.ps1          -> start (no-op if already running)
#   scripts\whisper-serve.ps1 -Stop    -> stop
#   scripts\whisper-serve.ps1 -Status  -> report health
param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$root     = Split-Path -Parent $PSScriptRoot          # <Alan root>
$py       = Join-Path $root 'env\whisper\python\python.exe'
$vendor   = Join-Path $root 'vendor\whisper-flow'
$port     = 8181
$ready    = "http://127.0.0.1:$port/ready"
$logDir   = Join-Path $root 'data\logs\whisper'
$pidFile  = Join-Path $logDir 'sidecar.pid'
$outLog   = Join-Path $logDir 'sidecar.out.log'
$errLog   = Join-Path $logDir 'sidecar.err.log'

function Test-Sidecar {
    try {
        Invoke-WebRequest -Uri $ready -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

if ($Status) {
    if (Test-Sidecar) {
        Write-Host "whisper sidecar: UP   http://127.0.0.1:$port" -ForegroundColor Green
        exit 0
    }
    Write-Host "whisper sidecar: DOWN" -ForegroundColor Yellow
    exit 1
}

if ($Stop) {
    $stopped = $false
    if (Test-Path $pidFile) {
        $sidecarPid = 0
        [void][int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref]$sidecarPid)
        if ($sidecarPid -gt 0) {
            try { Stop-Process -Id $sidecarPid -Force -ErrorAction Stop; $stopped = $true } catch { }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
    # Fallback: stop whatever owns the port.
    try {
        $owner = (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop | Select-Object -First 1).OwningProcess
        if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; $stopped = $true }
    } catch { }
    if ($stopped) { Write-Host "whisper sidecar stopped." -ForegroundColor Cyan }
    else { Write-Host "whisper sidecar was not running." -ForegroundColor Yellow }
    exit 0
}

if (-not (Test-Path $py)) {
    Write-Error "Sidecar Python not found at $py. Run scripts\setup-whisper.ps1 first."
    exit 1
}

if (-not (Test-Path $vendor)) {
    Write-Error "Pinned source not found at $vendor. Clone it first:`n  git clone https://github.com/dimastatz/whisper-flow.git vendor\whisper-flow"
    exit 1
}

if (Test-Sidecar) {
    Write-Host "whisper sidecar already up on port $port." -ForegroundColor Cyan
    exit 0
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# The pinned clone is authoritative: put it ahead of the installed copy in
# PYTHONPATH, so the server runs the exact commit vendor\whisper-flow pins
# rather than whatever a later `pip install` happened to leave behind.
$env:PYTHONPATH          = $vendor
$env:PYTHONUTF8          = '1'
# Keep bytecode caches out of the source tree: compile .pyc into host temp.
$env:PYTHONPYCACHEPREFIX = (Join-Path $env:TEMP 'alan-whisper-pyc')

# Local model, local files. The bundled tiny.en.pt ships inside the package;
# anything Whisper downloads (it should not, for tiny.en) lands under $vendor
# rather than the host's home directory.
$env:WF_MODEL            = 'tiny.en.pt'
$env:TORCH_HOME         = (Join-Path $logDir 'torch')

Write-Host "Starting whisper sidecar (Whisper tiny.en, port $port) ..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $py -ArgumentList '-m','uvicorn','whisperflow.fast_server:app','--host','127.0.0.1','--port',"$port" `
    -WorkingDirectory $vendor -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog

$proc.Id | Out-File -FilePath $pidFile -Encoding ascii

# The first start loads the Whisper model into RAM: a few seconds warm, longer
# on a cold disk. Wait generously, and only call it a failure once the process
# is actually gone -- "crashed" and "still starting" must not read the same.
$timeoutSec = 180
$ok = $false
$exited = $false
$lastNote = 0

for ($i = 0; $i -lt $timeoutSec; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Sidecar) { $ok = $true; break }
    if ($proc.HasExited) { $exited = $true; break }
    if (($i - $lastNote) -ge 30) {
        $lastNote = $i
        Write-Host ("  ... still starting ({0}s/{1}s)" -f $i, $timeoutSec) -ForegroundColor DarkGray
    }
}

if ($ok) {
    Write-Host "whisper sidecar ready: http://127.0.0.1:$port" -ForegroundColor Green
    exit 0
}

if ($exited) {
    Write-Error "whisper sidecar exited during startup (see $errLog)"
    if (Test-Path $errLog) { Get-Content $errLog -Tail 30 | ForEach-Object { Write-Host $_ } }
    exit 1
}

Write-Warning "whisper sidecar is still starting after ${timeoutSec}s (pid $($proc.Id))."
Write-Warning "It is running but not yet ready. Check $errLog, then re-run with -Status."
exit 2
