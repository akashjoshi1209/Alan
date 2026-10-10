# Alan - download an STT model for the whisper-flow sidecar
# ---------------------------------------------------------------------------
# whisper-flow serves whatever *.pt sits in its models directory, and GET /ready
# lists them. This script pulls an OpenAI Whisper checkpoint into that directory
# through the whisper library's own downloader (URL + hash check, nothing
# hand-rolled), then prints what the sidecar can now offer.
#
# Installed models live in vendor\whisper-flow\whisperflow\models, which is
# git-ignored with the rest of vendor\ -- models stay on this machine and never
# enter the repo.
#
# Usage:
#   scripts\pull-stt-model.ps1                -> download 'small' (multilingual)
#   scripts\pull-stt-model.ps1 base.en        -> an English-only variant
#   scripts\pull-stt-model.ps1 -List          -> what is installed already
param(
    [string]$Model = 'small',
    [switch]$List
)

$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent $PSScriptRoot
$py     = Join-Path $root 'env\whisper\python\python.exe'
$models = Join-Path $root 'vendor\whisper-flow\whisperflow\models'

function Get-Installed {
    if (-not (Test-Path $models)) { return @() }
    Get-ChildItem -Path $models -Filter *.pt -ErrorAction SilentlyContinue |
        ForEach-Object { "{0}  ({1:N0} bytes)" -f $_.Name, $_.Length }
}

if ($List) {
    $installed = @(Get-Installed)
    if ($installed.Count -eq 0) {
        Write-Host "No models installed at $models" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Installed STT models:" -ForegroundColor Cyan
    $installed | ForEach-Object { Write-Host "  $_" }
    exit 0
}

if (-not (Test-Path $py)) {
    Write-Error "Sidecar Python not found at $py. Run scripts\setup-whisper.ps1 first."
    exit 1
}
if (-not (Test-Path $models)) {
    Write-Error "Models directory not found at $models. Clone whisper-flow first:`n  git clone https://github.com/dimastatz/whisper-flow.git vendor\whisper-flow"
    exit 1
}

$target = Join-Path $models "$Model.pt"
if (Test-Path $target) {
    Write-Host "$Model.pt is already installed." -ForegroundColor Cyan
} else {
    Write-Host "Downloading Whisper '$Model' into the sidecar's models dir ..." -ForegroundColor Cyan
    Write-Host "(large models are hundreds of MB; run this only once)" -ForegroundColor DarkGray
    # load_model(name, download_root=...) is the library's own download path:
    # it verifies the checkpoint hash and writes <name>.pt into the models dir.
    # The model is then loaded and immediately dropped when the process exits --
    # the point here is the file, not a transcription.
    & $py -c "import whisper, sys; sys.exit(0 if whisper.load_model('$Model', download_root=r'$models') else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Download of '$Model' failed (unknown model name, or no network)."
        exit 1
    }
}

Write-Host ""
Write-Host "Models the sidecar will offer:" -ForegroundColor Cyan
@(Get-Installed) | ForEach-Object { Write-Host "  $_" }
Write-Host ""
Write-Host "Then, in config\settings.toml:  [voice] model = `"$Model.pt`"" -ForegroundColor Green
Write-Host "And restart the sidecar:        scripts\whisper-serve.ps1 -Stop; scripts\whisper-serve.ps1" -ForegroundColor Green
