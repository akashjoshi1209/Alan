# Alan - build the Whisper Flow (speech-to-text) sidecar environment
# ---------------------------------------------------------------------------
# Creates env\whisper\python (an isolated copy of Alan's bundled Python) and
# installs the whisper-flow stack into it: FastAPI server + OpenAI Whisper,
# with CPU-only PyTorch so Windows does not pull the ~2.4 GB CUDA build.
# Idempotent: safe to re-run.
#
# The pinned source lives in vendor\whisper-flow (git-ignored, same rule as
# vendor\ECC). If it is missing:
#   git clone https://github.com/dimastatz/whisper-flow.git vendor\whisper-flow
#
# Usage:  scripts\setup-whisper.ps1
$ErrorActionPreference = 'Stop'

$root  = Split-Path -Parent $PSScriptRoot
$srcPy = Join-Path $root 'env\python'
$dstPy = Join-Path $root 'env\whisper\python'
$py    = Join-Path $dstPy 'python.exe'
$src   = Join-Path $root 'vendor\whisper-flow'

if (-not (Test-Path (Join-Path $srcPy 'python.exe'))) {
    Write-Error "Base Python not found at $srcPy."
    exit 1
}

if (-not (Test-Path $src)) {
    Write-Error "Pinned source not found at $src. Clone it first:`n  git clone https://github.com/dimastatz/whisper-flow.git vendor\whisper-flow"
    exit 1
}

if (-not (Test-Path $py)) {
    Write-Host "Creating isolated sidecar Python (copy of base env) ..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path $dstPy) | Out-Null
    Copy-Item -Recurse -Force $srcPy $dstPy
} else {
    Write-Host "Sidecar Python already present at $dstPy" -ForegroundColor Cyan
}

# Drop Alan's src hook (.\..\..\src) from the copied ._pth, so the sidecar
# cannot import Alan's own modules. Run unconditionally, not only on a fresh
# copy: a re-run after a partial failure would otherwise leave the line in
# place. Read before writing -- piping Get-Content straight into Set-Content
# on the same path keeps the read handle open and fails on Windows.
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

# Judge failures by $LASTEXITCODE, not by stderr. Under 'Stop', pip's routine
# "scripts installed ... which is not on PATH" warning becomes a terminating
# error and kills a run that succeeded. The sidecar env is deliberately not on
# PATH, so that warning is expected.
$ErrorActionPreference = 'Continue'

Write-Host "Upgrading pip/setuptools/wheel ..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip "setuptools<70" wheel 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed."; exit 1 }

# openai-whisper needs setuptools < 70 to build; torch is installed first from
# the CPU index because a bare `pip install torch` on Windows resolves to the
# multi-gigabyte CUDA build.
Write-Host "Installing CPU-only PyTorch (large; several minutes) ..." -ForegroundColor Cyan
& $py -m pip install torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch install failed."; exit 1 }

Write-Host "Installing whisper-flow from the pinned vendor source ..." -ForegroundColor Cyan
& $py -m pip install $src 2>&1 | Select-Object -Last 8
if ($LASTEXITCODE -ne 0) { Write-Error "whisper-flow install failed."; exit 1 }

# The push-to-talk client speaks the /ws protocol; it lives in Alan's src tree
# and runs under this interpreter, so it needs the websocket client library.
Write-Host "Installing websocket-client (recorder client) ..." -ForegroundColor Cyan
& $py -m pip install websocket-client==1.9.0 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { Write-Error "websocket-client install failed."; exit 1 }

Write-Host ""
Write-Host "Sidecar environment ready." -ForegroundColor Green
Write-Host "Start it with: scripts\whisper-serve.ps1" -ForegroundColor Green
Write-Host "Then talk to Alan with: scripts\run.ps1 voice" -ForegroundColor Green
