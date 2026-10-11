# Alan - text-to-speech sidecar
# ---------------------------------------------------------------------------
# Runs a TTS server in its own environment (env\tts\python), on 127.0.0.1:8182.
# Clients POST text and get a WAV back; Alan's `say()` is that client. Nothing
# leaves the machine: the engine is local either way.
#
# Two engines answer the same port and the same protocol, and the choice is
# this flag -- which is the point of the seam. voice.py posts one HTTP request
# and does not know or care which server replies.
#
#   -Engine piper      (default) rhasspy/piper, a VITS voice: tens of MB of
#                      ONNX, synthesizes FASTER than real time on this CPU.
#                      Measured here: ~1.4 s to say ~7 s (RTF ~0.2). This is
#                      the engine that makes voice output usable on a 4 GB /
#                      2-core box. No voice design -- pick a voice by name.
#   -Engine omnivoice  k2-fsa/OmniVoice, the 613M diffusion model. Higher
#                      ceiling, but measured RTF > 356 here: ~18 minutes for a
#                      3-second sentence. Kept for a machine with a GPU; it is
#                      not usable on this one. See docs\integrations\tts.md.
#
# This is the outbound half of voice. The inbound half is whisper-flow on
# 8181 -- ADR-003's rule (separate process, separate environment, loopback
# HTTP), applied a third time. The sidecar is deliberately NOT auto-started:
# even the small engine is resident while it is up, and a background service
# should exist only when someone is actually listening.
#
# Ports: 8182 here, whisper 8181, cognee 8012, browser 8022, Ollama 11435 --
# no overlap.
#
# Usage:
#   scripts\tts-serve.ps1                          -> start Piper (no-op if running)
#   scripts\tts-serve.ps1 -Engine omnivoice        -> start the OmniVoice engine
#   scripts\tts-serve.ps1 -Voice en_US-amy-medium  -> Piper, a different voice
#   scripts\tts-serve.ps1 -Stop                    -> stop
#   scripts\tts-serve.ps1 -Status                  -> report health
param(
    [ValidateSet('piper', 'omnivoice')]
    [string]$Engine = 'piper',
    #: Piper voice name; empty uses the server's own default (en_US-ryan-medium).
    [string]$Voice = '',
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$root     = Split-Path -Parent $PSScriptRoot          # <Alan root>
$py       = Join-Path $root 'env\tts\python\python.exe'
$model    = Join-Path $root 'data\tts\model'          # OmniVoice weights
$voices   = Join-Path $root 'data\tts\piper'          # Piper voices
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
if (Test-ServerAlive) {
    Write-Host "TTS sidecar already running on 127.0.0.1:$port" -ForegroundColor Cyan
    Write-Host "Only one engine can hold the port: run scripts\tts-serve.ps1 -Stop to switch." -ForegroundColor DarkGray
    exit 0
}

if ($Engine -eq 'omnivoice') {
    $server = Join-Path $root 'src\tts_server.py'
    if (-not (Test-Path $server)) {
        Write-Error "Sidecar server not found at $server."
        exit 1
    }

    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

    # The model path comes from settings if it is set, else from data\tts\model
    # if it has been downloaded, else the HuggingFace repo id (which would then
    # fetch at start -- 3 GB the first time). Naming the local directory is what
    # makes the start fast.
    $env:TTS_HOST = '127.0.0.1'
    $env:TTS_PORT = "$port"
    if (Test-Path (Join-Path $model 'model.safetensors')) {
        $env:TTS_MODEL = $model
    } else {
        $env:TTS_MODEL = 'k2-fsa/OmniVoice'
        Write-Host "Local model not found under $model; using the repo id." -ForegroundColor Yellow
        Write-Host "Run scripts\pull-tts-model.ps1 to download it once." -ForegroundColor Yellow
    }

    # float16 by default -- measured on this machine, not assumed. See the note
    # at the top of src\tts_server.py: CPU fp16 is emulated (so it buys no
    # speed), but it halves the working set, which on 3.9 GB of RAM decides
    # whether the ~1.5 GB of weights stays resident or pages against the disk.
    if (-not $env:TTS_DTYPE) { $env:TTS_DTYPE = 'float16' }
    $describe = "model=$($env:TTS_MODEL) dtype=$($env:TTS_DTYPE)"
} else {
    $server = Join-Path $root 'src\tts_server_piper.py'
    if (-not (Test-Path $server)) {
        Write-Error "Sidecar server not found at $server."
        exit 1
    }

    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

    $voiceName = if ($Voice) { $Voice } else { 'en_US-ryan-medium' }
    $env:TTS_HOST = '127.0.0.1'
    $env:TTS_PORT = "$port"
    $env:TTS_VOICE = $voiceName
    $env:TTS_VOICES_DIR = $voices
    if (Test-Path (Join-Path $voices "$voiceName.onnx")) {
        # nothing to do: the voice is on disk
    } else {
        Write-Host "Voice '$voiceName' not found under $voices." -ForegroundColor Yellow
        Write-Host "Run scripts\pull-tts-voice.ps1 -Voice $voiceName to download it once." -ForegroundColor Yellow
    }
    $describe = "voice=$voiceName"
}

$errAction = 'Continue'   # stderr from Start-Process redirection is not a failure
$p = Start-Process -FilePath $py `
                   -ArgumentList @($server) `
                   -WindowStyle Hidden `
                   -RedirectStandardOutput $logFile `
                   -RedirectStandardError $errFile `
                   -PassThru
Set-Content -Path $pidFile -Value $p.Id
Write-Host "TTS sidecar starting (pid $($p.Id), engine=$Engine, $describe)" -ForegroundColor Cyan

# Wait for the listener. OmniVoice can take minutes to load 3 GB from disk;
# Piper is resident in well under a second. One generous budget covers both,
# and a fast engine simply returns early.
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
