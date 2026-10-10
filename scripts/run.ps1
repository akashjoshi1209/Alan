# Alan launcher
# Usage:  scripts\run.ps1              -> interactive chat
#         scripts\run.ps1 ask "hi"     -> one-shot question
#         scripts\run.ps1 providers    -> show backend status
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot          # <Alan root>
$pyDir = Join-Path $root 'env\python'
$py = Join-Path $pyDir 'python.exe'

# Alan's bundled Ollama uses its own port so it never collides with a
# host-installed Ollama (which normally listens on 11434).
$ollamaPort = 11435

if (-not (Test-Path $py)) {
    Write-Error "Alan's Python not found at $py. Re-run the setup."
    exit 1
}

# Make the whole thing self-contained: nothing is read from the host install.
$env:ALAN_HOME  = $root
$env:PYTHONPATH = (Join-Path $root 'src')
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PATH = $pyDir + ';' + (Join-Path $pyDir 'Scripts') + ';' + $env:PATH

# Start the bundled Ollama server (models live in models\) if it is not already up.
$ollamaExe = Join-Path $root 'env\ollama\ollama.exe'
if ((Test-Path $ollamaExe) -and ($env:ALAN_NO_OLLAMA -ne '1')) {
    $env:OLLAMA_MODELS = (Join-Path $root 'models')
    $env:OLLAMA_HOST   = "127.0.0.1:$ollamaPort"
    $listening = $false
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$ollamaPort/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $listening = $true
    } catch { }
    if (-not $listening) {
        try { Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden } catch { }
        # Wait for the bundled server to accept connections (first launch can take a few seconds).
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            try {
                Invoke-WebRequest -Uri "http://127.0.0.1:$ollamaPort/api/tags" -UseBasicParsing -TimeoutSec 3 | Out-Null
                break
            } catch { }
        }
    }
}

# Start the Cognee memory sidecar for chat/ask (keyless local memory).
$cmd = if ($args.Count -eq 0) { 'chat' } else { [string]$args[0] }
$cogneePy = Join-Path $root 'env\cognee\python\python.exe'
if (($cmd -in @('chat', 'ask')) -and ($env:ALAN_NO_MEMORY -ne '1') -and (Test-Path $cogneePy)) {
    $serve = Join-Path $root 'scripts\cognee-serve.ps1'
    if (Test-Path $serve) {
        try { & $serve | Out-Null } catch { Write-Warning "Memory sidecar did not start; continuing without memory." }
    }
}

# Start the Whisper speech-to-text sidecar for `voice` (local transcription).
# Unlike cognee this is not silenced: the model loads in seconds, and a first
# run that printed nothing for half a minute would read as a hang.
$whisperPy = Join-Path $root 'env\whisper\python\python.exe'
if (($cmd -eq 'voice') -and ($env:ALAN_NO_VOICE -ne '1') -and (Test-Path $whisperPy)) {
    $serve = Join-Path $root 'scripts\whisper-serve.ps1'
    if (Test-Path $serve) {
        try { & $serve } catch { Write-Warning "Voice sidecar did not start; `alan voice` will say so." }
    }
}

if ($args.Count -eq 0) {
    & $py -m alan chat
} else {
    & $py -m alan @args
}
exit $LASTEXITCODE
