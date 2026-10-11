# Alan - download the OmniVoice TTS model for the sidecar
# ---------------------------------------------------------------------------
# OmniVoice's from_pretrained accepts a HuggingFace repo id or a local
# directory. This script resolves it once, into data\tts\model, so the ~3 GB
# of weights lands on this machine instead of being re-fetched per start, and
# so the sidecar can be told to load a path it already has.
#
# data\ is git-ignored with the rest of the runtime tree: models stay on this
# machine and never enter the repo.
#
# Downloads ~3.04 GB: a 2.28 GiB diffusion backbone (613M params, Qwen3-0.6B
# based) plus a 768 MiB audio tokenizer. Both are required -- the tokenizer
# turns the model's tokens back into waveforms, so skipping it would leave a
# model that loads and then cannot be heard.
#
# The fetch itself lives in src\pull_tts_model.py rather than in a
# huggingface_hub snapshot_download call, and the reason is worth stating here
# where an operator will read it before running: the hub fans out with a thread
# pool, and on this machine every new outbound web connection costs a fixed
# ~10.5 s to Quick Heal's TLS inspection (the same tax behind alan ask's old
# "request timed out"). Pooling workers pays that repeatedly. The script uses
# one pooled connection instead, measured at ~4.3 MB/s steady, which is why
# this takes ~12 minutes rather than never finishing. It also resumes: an
# interrupted run continues from the partial file rather than starting over.
#
# Usage:
#   scripts\pull-tts-model.ps1          -> download (idempotent, resumes)
#   scripts\pull-tts-model.ps1 -List    -> what is installed already
param(
    [string]$Model = 'k2-fsa/OmniVoice',
    [switch]$List
)

$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent $PSScriptRoot
$py     = Join-Path $root 'env\tts\python\python.exe'
$pullScript = Join-Path $root 'src\pull_tts_model.py'
$models = Join-Path $root 'data\tts\model'

if (-not (Test-Path $py)) {
    Write-Error "Sidecar Python not found at $py. Run scripts\setup-tts.ps1 first."
    exit 1
}
if (-not (Test-Path $pullScript)) {
    Write-Error "Downloader not found at $pullScript."
    exit 1
}

if ($List) {
    & $py $pullScript --model $Model --dest $models --list
    exit $LASTEXITCODE
}

Write-Host "Fetching $Model into $models" -ForegroundColor Cyan
Write-Host "(one connection at a time, resumable -- see the header of pull-tts-model.ps1)" -ForegroundColor DarkGray

# Judge by $LASTEXITCODE, not by stderr: pip-style and tqdm-style output goes
# to stderr even on success, and under 'Stop' a warning would otherwise be
# read as a failed download of 3 GB that actually landed.
$ErrorActionPreference = 'Continue'
& $py $pullScript --model $Model --dest $models
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Error "Download did not complete (exit $code). Re-run to resume from where it stopped."
    exit $code
}

Write-Host ""
Write-Host "Then point the sidecar at it:" -ForegroundColor Green
Write-Host "  [voice] tts_model = `"$($models -replace '\\','\\')`"" -ForegroundColor DarkGray
Write-Host "And start it with: scripts\tts-serve.ps1" -ForegroundColor Green
