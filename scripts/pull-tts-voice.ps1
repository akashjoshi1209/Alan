# Alan - download Piper voices for the CPU TTS sidecar
# ---------------------------------------------------------------------------
# Piper voices are a couple of small files each -- <voice>.onnx plus a
# <voice>.onnx.json config -- of the order of 20-60 MB, against the ~3 GB of
# the OmniVoice model. They land in data\tts\piper, which is git-ignored with
# the rest of the runtime tree: voices stay on this machine.
#
# The fetch is piper's own `python -m piper.download_voices`, run under the
# sidecar interpreter. Unlike the OmniVoice puller in src\pull_tts_model.py,
# this does not need a custom single-connection downloader: piper fetches a
# handful of small files sequentially, so the ~10.5 s TLS-handshake tax Quick
# Hearth's web driver inserts here is paid a few times, not hundreds.
#
# Voices worth knowing (all English; -medium is ~RTF 0.2 here, -high is more
# expressive at ~RTF 1.2):
#   en_US-ryan-medium        default; male, clear, reads "Alan"
#   en_US-hfc_female-medium  studio-quality female
#   en_US-amy-medium         female
#   en_US-lessac-medium      neutral, the classic Piper demo voice
#   en_GB-alba-medium        British female
#   en_US-ryan-high          male, more expressive, ~6x slower than -medium
#
# Pick a voice here, then name the same one at start:
#   scripts\pull-tts-voice.ps1 -Voice en_US-hfc_female-medium
#   scripts\tts-serve.ps1 -Voice en_US-hfc_female-medium
#
# Usage:
#   scripts\pull-tts-voice.ps1                        -> the default voice
#   scripts\pull-tts-voice.ps1 -Voice a, b            -> several at once
#   scripts\pull-tts-voice.ps1 -Voice x -Force        -> re-download x
param(
    [string[]]$Voice = @('en_US-ryan-medium'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$root    = Split-Path -Parent $PSScriptRoot          # <Alan root>
$py      = Join-Path $root 'env\tts\python\python.exe'
$voices  = Join-Path $root 'data\tts\piper'

if (-not (Test-Path $py)) {
    Write-Error "Sidecar Python not found at $py. Run scripts\setup-tts.ps1 first."
    exit 1
}

New-Item -ItemType Directory -Force -Path $voices | Out-Null

$args = @('-m', 'piper.download_voices', '--download-dir', $voices)
if ($Force) { $args += '--force-redownload' }
$args += $Voice

Write-Host "Downloading Piper voice(s): $($Voice -join ', ')" -ForegroundColor Cyan

# piper logs progress on stderr and prints nothing on stdout; a non-zero
# exit code is the real failure signal, so do not treat stderr as an error.
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $py @args 2>&1 | ForEach-Object { Write-Host "  $_" }
$code = $LASTEXITCODE
$ErrorActionPreference = $prev

if ($code -ne 0) {
    Write-Error "piper.download_voices exited $code. Check the voice name (e.g. en_US-amy-medium)."
    exit $code
}

foreach ($name in $Voice) {
    $onnx = Join-Path $voices "$name.onnx"
    if (Test-Path $onnx) {
        $mb = [math]::Round((Get-Item $onnx).Length / 1MB, 1)
        Write-Host "  ok: $name.onnx ($mb MB)" -ForegroundColor Green
    } else {
        Write-Host "  missing: $onnx" -ForegroundColor Yellow
    }
}

Write-Host "Voices in $voices" -ForegroundColor Cyan
