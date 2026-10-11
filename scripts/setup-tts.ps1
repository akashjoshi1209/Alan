# Alan - build the OmniVoice (text-to-speech) sidecar environment
# ---------------------------------------------------------------------------
# Creates env\tts\python (an isolated copy of Alan's bundled Python) and
# installs the OmniVoice inference stack into it, with CPU-only PyTorch so
# Windows does not pull the ~2.4 GB CUDA build. Idempotent: safe to re-run.
#
# This is the outbound half of voice. The inbound half is whisper-flow on
# 8181 (scripts\setup-whisper.ps1); this is the same ADR-003 rule applied a
# third time -- a model stack that is not Alan's does not share Alan's
# interpreter, because torch and transformers have no business in the main
# environment and neither does a 2-3 GB working set.
#
# The pinned source lives in vendor\OmniVoice (git-ignored, same rule as
# vendor\whisper-flow and vendor\ECC). If it is missing:
#   git clone https://github.com/k2-fsa/OmniVoice.git vendor\OmniVoice
#
# Installed lean on purpose. OmniVoice's pyproject pulls gradio, tensorboardX
# and webdataset, all three of which exist only for `omnivoice-demo` (the
# Gradio UI) and for training. Alan calls the Python API directly, so the
# package is installed with --no-deps and only the imports the inference path
# actually touches are installed underneath it: models/omnivoice.py needs
# numpy, torch and torchaudio, and from_pretrained needs transformers and
# accelerate for the tokenizer, feature extractor and device_map.
#
# Usage:  scripts\setup-tts.ps1
$ErrorActionPreference = 'Stop'

$root  = Split-Path -Parent $PSScriptRoot
$srcPy = Join-Path $root 'env\python'
$dstPy = Join-Path $root 'env\tts\python'
$py    = Join-Path $dstPy 'python.exe'
$src   = Join-Path $root 'vendor\OmniVoice'

if (-not (Test-Path (Join-Path $srcPy 'python.exe'))) {
    Write-Error "Base Python not found at $srcPy."
    exit 1
}

if (-not (Test-Path $src)) {
    Write-Error "Pinned source not found at $src. Clone it first:`n  git clone https://github.com/k2-fsa/OmniVoice.git vendor\OmniVoice"
    exit 1
}

if (-not (Test-Path $py)) {
    Write-Host "Creating isolated sidecar Python (copy of base env) ..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path $dstPy) | Out-Null
    Copy-Item -Recurse -Force $srcPy $dstPy
} else {
    Write-Host "Sidecar Python already present at $dstPy" -ForegroundColor Cyan
}

# Drop Alan's src hook (..\..\src) from the copied ._pth, so the sidecar
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

# torch first, from the CPU index: a bare `pip install torch` on Windows
# resolves to the multi-gigabyte CUDA build, and this machine has Intel HD
# Graphics 520 -- no CUDA device would ever be used, so the download would be
# pure waste. torchaudio comes from the same index so the two agree on build.
Write-Host "Installing CPU-only PyTorch + torchaudio (large; several minutes) ..." -ForegroundColor Cyan
& $py -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch install failed."; exit 1 }

# The inference-only dependency set. transformers>=5.3.0 is what OmniVoice's
# own pyproject asks for; do not relax it, its config loading depends on that
# major version.
Write-Host "Installing inference dependencies ..." -ForegroundColor Cyan
& $py -m pip install "transformers>=5.3.0" accelerate numpy soundfile librosa pydub 2>&1 | Select-Object -Last 4
if ($LASTEXITCODE -ne 0) { Write-Error "dependency install failed."; exit 1 }

# --no-deps is the point of the lean set above: the pinned clone is installed
# as source, but its declared dependencies (gradio, tensorboardX, webdataset)
# are deliberately not resolved, since nothing Alan runs imports them.
#
# hatchling is installed explicitly and build isolation switched off. OmniVoice
# builds with hatchling and pip would normally fetch it into a throwaway build
# environment, but that fetch is a network round trip at exactly the moment
# this machine is already paying the HTTPS tax -- and a build that cannot
# import its own backend fails with a message that names neither the missing
# package nor the fix. Installing it first removes the round trip.
Write-Host "Installing the hatchling build backend ..." -ForegroundColor Cyan
& $py -m pip install hatchling 2>&1 | Select-Object -Last 2
if ($LASTEXITCODE -ne 0) { Write-Error "hatchling install failed."; exit 1 }

Write-Host "Installing OmniVoice from the pinned vendor source (--no-deps) ..." -ForegroundColor Cyan
& $py -m pip install --no-deps --no-build-isolation $src 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { Write-Error "OmniVoice install failed."; exit 1 }

# Prove the inference path imports before declaring success. A missing
# dependency otherwise surfaces at the operator's first spoken answer, as a
# sidecar that never becomes ready.
#
# Written to a file rather than passed as `python -c "..."`: PowerShell strips
# the inner quotes and treats the `|` in the string as a pipe, so the probe
# arrives at Python already mangled and reports a SyntaxError that has nothing
# to do with whether OmniVoice imports.
Write-Host "Verifying the inference path imports ..." -ForegroundColor Cyan
$probeFile = Join-Path $env:TEMP 'alan-tts-import-probe.py'
@'
import torch
import torchaudio
import transformers
import soundfile
import numpy
import omnivoice

print("omnivoice", omnivoice.__version__)
print("torch     ", torch.__version__)
print("cuda      ", torch.cuda.is_available())
'@ | Set-Content -Path $probeFile -Encoding ascii
& $py $probeFile
if ($LASTEXITCODE -ne 0) { Write-Error "OmniVoice failed to import; see the output above."; exit 1 }

Write-Host ""
Write-Host "Sidecar environment ready." -ForegroundColor Green
Write-Host "Download the model once with: scripts\pull-tts-model.ps1" -ForegroundColor Green
Write-Host "Start it with: scripts\tts-serve.ps1" -ForegroundColor Green
