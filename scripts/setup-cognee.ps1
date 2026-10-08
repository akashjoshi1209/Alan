# Alan - build the Cognee memory sidecar environment
# ---------------------------------------------------------------------------
# Creates env\cognee\python (an isolated copy of Alan's portable Python) and
# installs the cognee[gliner] stack into it, with CPU-only PyTorch so Windows
# does not pull the ~2.4 GB CUDA build. Idempotent: safe to re-run.
#
# Usage:  scripts\setup-cognee.ps1
$ErrorActionPreference = 'Stop'

$root  = Split-Path -Parent $PSScriptRoot
$srcPy = Join-Path $root 'env\python'
$dstPy = Join-Path $root 'env\cognee\python'
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

# Drop Alan's src hook (.\..\..\src) from the copied ._pth, so the sidecar
# cannot import Alan's own modules. Run unconditionally, not only on a fresh
# copy: doing it only inside the copy branch meant a re-run after a partial
# failure left the src line in place. The read is materialised before writing,
# because piping Get-Content straight into Set-Content on the same path keeps
# the read handle open and fails with "used by another process" on Windows.
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
& $py -m pip install --upgrade pip setuptools wheel 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed."; exit 1 }

Write-Host "Installing CPU-only PyTorch ..." -ForegroundColor Cyan
& $py -m pip install torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { Write-Error "PyTorch install failed."; exit 1 }

Write-Host "Installing cognee[gliner] (large; several minutes) ..." -ForegroundColor Cyan
& $py -m pip install "cognee[gliner]" 2>&1 | Select-Object -Last 10
if ($LASTEXITCODE -ne 0) { Write-Error "cognee install failed."; exit 1 }

# Windows / embeddable-Python fix: cognee imports ladybug while running its
# startup migrations, and ladybug's native extension needs the OpenSSL 3 DLLs
# in <python>\DLLs. The embeddable distribution ships them in the Python root
# instead (its DLLs\ directory does not exist), so copy them into place.
$dlls = Join-Path $dstPy 'DLLs'
New-Item -ItemType Directory -Force -Path $dlls | Out-Null
foreach ($name in @('libssl-3.dll', 'libcrypto-3.dll')) {
    $src = Join-Path $dstPy $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $dlls $name) -Force }
}

Write-Host ""
Write-Host "Sidecar environment ready." -ForegroundColor Green
Write-Host "Start it with: scripts\cognee-serve.ps1" -ForegroundColor Green
Write-Host "The first memory write downloads the GLiNER extractor (~750 MB) and the" -ForegroundColor Green
Write-Host "fastembed model (~67 MB) into data\cognee\cache; later writes are quick." -ForegroundColor Green
