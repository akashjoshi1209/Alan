# Alan - Cognee memory sidecar
# ---------------------------------------------------------------------------
# Runs cognee's REST API in keyless local mode (GLiNER extraction + fastembed
# embeddings, no cloud LLM) as a background process, with all stores and model
# caches kept inside the Alan folder. Everything is derived from this script's
# own location, so it works from any drive (D:, the USB, etc.).
#
# Usage:
#   scripts\cognee-serve.ps1          -> start (no-op if already running)
#   scripts\cognee-serve.ps1 -Stop    -> stop
#   scripts\cognee-serve.ps1 -Status  -> report health
param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$root    = Split-Path -Parent $PSScriptRoot          # <Alan root>
$pyDir   = Join-Path $root 'env\cognee\python'
$py      = Join-Path $pyDir 'python.exe'
$port    = 8012
$health  = "http://127.0.0.1:$port/health"
$cogneeRoot = Join-Path $root 'data\cognee'
$logDir  = Join-Path $cogneeRoot 'logs'
$pidFile = Join-Path $cogneeRoot 'sidecar.pid'
$outLog  = Join-Path $logDir 'sidecar.out.log'
$errLog  = Join-Path $logDir 'sidecar.err.log'

function Test-Sidecar {
    try {
        Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

if ($Status) {
    if (Test-Sidecar) {
        Write-Host "cognee sidecar: UP   http://127.0.0.1:$port" -ForegroundColor Green
        exit 0
    }
    Write-Host "cognee sidecar: DOWN" -ForegroundColor Yellow
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
    if ($stopped) { Write-Host "cognee sidecar stopped." -ForegroundColor Cyan }
    else { Write-Host "cognee sidecar was not running." -ForegroundColor Yellow }
    exit 0
}

if (-not (Test-Path $py)) {
    Write-Error "Sidecar Python not found at $py. Run scripts\setup-cognee.ps1 first."
    exit 1
}

if (Test-Sidecar) {
    Write-Host "cognee sidecar already up on port $port." -ForegroundColor Cyan
    exit 0
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $cogneeRoot 'system') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $cogneeRoot 'data') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $cogneeRoot 'cache\hf') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $cogneeRoot 'cache\fastembed') | Out-Null

# --- Keyless local configuration -------------------------------------------------
$env:ENABLE_BACKEND_ACCESS_CONTROL = 'false'
$env:REQUIRE_AUTHENTICATION        = 'false'
$env:AUTO_FEEDBACK                 = 'false'
$env:TELEMETRY_DISABLED            = 'true'

# Local extraction (GLiNER) and local CPU embeddings (fastembed). No LLM.
$env:GRAPH_EXTRACTOR               = 'gliner_demo'
$env:GLINER_AUTO_INSTALL           = 'false'
$env:EMBEDDING_PROVIDER            = 'fastembed'
$env:EMBEDDING_MODEL               = 'BAAI/bge-small-en-v1.5'
$env:EMBEDDING_DIMENSIONS          = '384'

# Embedded stores.
$env:DB_PROVIDER                   = 'sqlite'
$env:VECTOR_DB_PROVIDER            = 'lancedb'
$env:GRAPH_DATABASE_PROVIDER       = 'kuzu'

# All state + model caches live inside the Alan folder.
$env:SYSTEM_ROOT_DIRECTORY         = (Join-Path $cogneeRoot 'system')
$env:DATA_ROOT_DIRECTORY           = (Join-Path $cogneeRoot 'data')
$env:HF_HOME                       = (Join-Path $cogneeRoot 'cache\hf')
$env:FASTEMBED_CACHE_PATH          = (Join-Path $cogneeRoot 'cache\fastembed')
$env:TIKTOKEN_CACHE_DIR            = (Join-Path $cogneeRoot 'cache\tiktoken')
$env:COGNEE_LOGS_DIR               = $logDir

# Keep Python bytecode caches off the (slow, exFAT) USB: compile .pyc into the
# host temp dir instead of writing thousands of small files next to the sources.
$env:PYTHONPYCACHEPREFIX           = (Join-Path $env:TEMP 'alan-cognee-pyc')

# Server bind.
$env:HTTP_API_HOST                 = '127.0.0.1'
$env:HTTP_API_PORT                 = "$port"

# Guarantee keyless mode: drop anything that could flip cognee to a cloud LLM.
Remove-Item -Path Env:OPENAI_API_KEY, Env:LLM_API_KEY, Env:ANTHROPIC_API_KEY, Env:LLM_PROVIDER -ErrorAction SilentlyContinue

Write-Host "Starting cognee sidecar (GLiNER + fastembed, port $port) ..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $py -ArgumentList '-m','cognee.api.client' `
    -WorkingDirectory (Join-Path $root 'env\cognee') -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog

$proc.Id | Out-File -FilePath $pidFile -Encoding ascii

# cognee's cold start is genuinely slow: relational migrations, then the
# GLiNER extractor and fastembed model load from disk. Measured at roughly
# 3-4 minutes on this machine. The old 60-second wait gave up while the sidecar
# was still booting and reported failure for a process that was about to become
# healthy -- a false alarm that reads exactly like a real outage.
#
# What matters is the difference between "crashed" and "still starting", so we
# wait generously and only call it a failure once the process is actually gone.
$timeoutSec = 600
$ok = $false
$exited = $false
$lastNote = 0

for ($i = 0; $i -lt $timeoutSec; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Sidecar) { $ok = $true; break }
    if ($proc.HasExited) { $exited = $true; break }
    # A heartbeat every 30s, so a slow boot does not look like a hang.
    if (($i - $lastNote) -ge 30) {
        $lastNote = $i
        Write-Host ("  ... still starting ({0}s/{1}s)" -f $i, $timeoutSec) -ForegroundColor DarkGray
    }
}

if ($ok) {
    Write-Host "cognee sidecar ready: http://127.0.0.1:$port" -ForegroundColor Green
    exit 0
}

if ($exited) {
    Write-Error "cognee sidecar exited during startup (see $errLog)"
    if (Test-Path $errLog) { Get-Content $errLog -Tail 30 | ForEach-Object { Write-Host $_ } }
    exit 1
}

# Still running, still not answering /health. Do not claim failure, and do not
# claim success either.
Write-Warning "cognee sidecar is still starting after ${timeoutSec}s (pid $($proc.Id))."
Write-Warning "It is running but not yet healthy. Check $errLog, then re-run with -Status."
exit 2
