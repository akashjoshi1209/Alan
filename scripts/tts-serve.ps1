# Alan - OmniVoice text-to-speech sidecar
# ---------------------------------------------------------------------------
# Runs src\tts_server.py (Alan's own server, not OmniVoice's: the upstream
# ships only a Gradio demo and a CLI, neither of which is an API a client can
# call) in its own environment (env\tts\python), on 127.0.0.1:8182. Clients
# POST text and get a WAV back; Alan's `say()` is that client. Nothing leaves
# the machine: the model is OmniVoice itself, loaded locally.
#
# This is the outbound half of voice. The inbound half is whisper-flow on
# 8181 -- ADR-003's rule (separate process, separate environment, loopback
# HTTP), applied a third time. The sidecar is deliberately NOT auto-started:
# the model is ~2.5 GB resident while it is up, and on a 4 GB box a background
# service that holds it would starve everything else for the sake a feature
# that is used only when someone is actually listening.
#
# Ports: 8182 here, whisper 8181, cognee 8012, browser 8022, Ollama 11435 --
# no overlap.
#
# Usage:
#   scripts\tts-serve.ps1          -> start (no-op if already running)
#   scripts\tts-serve.ps1 -Stop    -> stop
#   scripts\tts-serve.ps1 -Status  -> report health
param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$root     = Split-Path -Parent $PSScriptRoot          # <Alan root>
$py       = Join-Path $root 'env\tts\python\python.exe'
$server   = Join-Path $root 'src\tts_server.py'
$model    = Join-Path $root 'data\tts\model'
$port     = 8182
$stateDir = Join-Path $root 'data\logs\tts'
$pidFile  = Join-Path $stateDir 'tts.pid'
$logFile  = Join-Path $stateDir 'tts.log'
$errFile  = Join-Path $stateDir 'tts.err.log'

function Test-ServerAlive {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/ready" -TimeoutSec 3 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if ($Status) {
    if (Test-ServerAlive) {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/ready" -TimeoutSec 3 -UseBasicParsing
        Write-Host "TTS sidecar is up on 127.0.0.1:$port" -ForegroundColor Green
        Write-Host $r.Content
    } else {
        Write-Host "TTS sidecar is not reachable on 127.0.0.1:$port" -ForegroundColor Yellow
        if (Test-Path $errFile) {
            Write-Host "--- last lines of $errFile ---" -ForegroundColor DarkGray
            Get-Content $errFile -Tail 15 | ForEach-Object { Write-Host "  $_" }
        }
    }
    exit 0
}

if ($Stop) {
    $stopped = $false
    if (Test-Path $pidFile) {
        $procId = Get-Content $pidFile -ErrorAction SilentlyContinue
        if ($procId) {
            $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($p) { Stop-Process -Id $procId -Force; $stopped = $true }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
    # A stale PID file (harness-killed shell, reboot) leaves a listener behind;
    # clear it by port so -Status never reports a sidecar that is half gone.
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $conn | ForEach-Object {
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
    }
    if ($stopped) { Write-Host "TTS sidecar stopped." -ForegroundColor Cyan }
    else          { Write-Host "TTS sidecar was not running." -ForegroundColor DarkGray }
    exit 0
}

# ---- start -----------------------------------------------------------------
if (-not (Test-Path $py)) {
    Write-Error "Sidecar Python not found at $py. Run scripts\setup-tts.ps1 first."
    exit 1
}
if (-not (Test-Path $server)) {
    Write-Error "Sidecar server not found at $server."
    exit 1
}
if (Test-ServerAlive) {
    Write-Host "TTS sidecar already running on 127.0.0.1:$port" -ForegroundColor Cyan
    exit 0
}

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

# The model path comes from settings if it is set, else from data\tts\model
# if it has been downloaded, else the HuggingFace repo id (which would then
# fetch at start -- 3 GB the first time, and a stall every time after if the
# cache is cold). Naming the local directory is what makes the start fast.
$env:TTS_HOST = '127.0.0.1'
$env:TTS_PORT = "$port"
if (Test-Path (Join-Path $model 'model.safetensors')) {
    $env:TTS_MODEL = $model
} else {
    $env:TTS_MODEL = 'k2-fsa/OmniVoice'
    Write-Host "Local model not found under $model; using the repo id." -ForegroundColor Yellow
    Write-Host "Run scripts\pull-tts-model.ps1 to download it once." -ForegroundColor Yellow
}

# float16 by default -- measured on this machine, not assumed. See the note at
# the top of src\tts_server.py: CPU fp16 is emulated (so it buys no speed), but
# it halves the working set, which on 3.9 GB of RAM decides whether the ~1.5 GB
# of weights stays resident or pages against the disk. float32 asks for 3.2 GB.
# Override with a measurement of your own:
#   $env:TTS_DTYPE='float32'; scripts\tts-serve.ps1
if (-not $env:TTS_DTYPE) { $env:TTS_DTYPE = 'float16' }

$errAction = 'Continue'   # stderr from Start-Process redirection is not a failure
$p = Start-Process -FilePath $py `
                   -ArgumentList @($server) `
                   -WindowStyle Hidden `
                   -RedirectStandardOutput $logFile `
                   -RedirectStandardError $errFile `
                   -PassThru
Set-Content -Path $pidFile -Value $p.Id
Write-Host "TTS sidecar starting (pid $($p.Id), model=$($env:TTS_MODEL), dtype=$env:TTS_DTYPE)" -ForegroundColor Cyan

# Wait for the listener: a model load is 3 GB from disk on first run and can
# take minutes, so this polls with a generous budget rather than assuming.
$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline) {
    if (Test-ServerAlive) {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/ready" -TimeoutSec 3 -UseBasicParsing
        Write-Host "TTS sidecar ready." -ForegroundColor Green
        Write-Host $r.Content
        exit 0
    }
    if ($p.HasExited) { break }
    Start-Sleep -Milliseconds 750
}

Write-Host "TTS sidecar did not become ready within 240s." -ForegroundColor Yellow
if (Test-Path $errFile) {
    Write-Host "--- last lines of $errFile ---" -ForegroundColor DarkGray
    Get-Content $errFile -Tail 20 | ForEach-Object { Write-Host "  $_" }
}
exit 1
