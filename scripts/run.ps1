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

# A port check, not a web request: TcpClient's connect is the only thing the
# launcher needs, and it answers in milliseconds either way. (An
# Invoke-WebRequest round-trip here used to be most of the launcher's
# non-Python time.)
function Test-Port {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return ($task.Wait(750) -and $client.Connected)
    } catch {
        return $false   # refused or faulted: the port is not serving
    } finally {
        $client.Dispose()
    }
}

# Start the bundled Ollama server (models live in models\) if it is not
# already up -- but never *wait* for it: a cloud-provider chat does not need
# it, and a local-model request that arrives before it is ready fails with a
# message instead of a prompt that sits frozen for half a minute.
$ollamaExe = Join-Path $root 'env\ollama\ollama.exe'
if ((Test-Path $ollamaExe) -and ($env:ALAN_NO_OLLAMA -ne '1')) {
    $env:OLLAMA_MODELS = (Join-Path $root 'models')
    $env:OLLAMA_HOST   = "127.0.0.1:$ollamaPort"
    if (-not (Test-Port $ollamaPort)) {
        try { Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden } catch { }
    }
}

# Start the Cognee memory sidecar for chat/ask (keyless local memory).
#
# Detached and never waited on, and completely silent when it is already up:
# opening a chat window must not queue behind a sidecar's boot or narrate it.
# Until cognee answers, memory falls back to the local JSON store -- `alan
# doctor` names which one is in use, so the fallback is visible, never silent.
$cmd = if ($args.Count -eq 0) { 'chat' } else { [string]$args[0] }
$cogneePy = Join-Path $root 'env\cognee\python\python.exe'
if (($cmd -in @('chat', 'ask')) -and ($env:ALAN_NO_MEMORY -ne '1') -and (Test-Path $cogneePy)) {
    $serve = Join-Path $root 'scripts\cognee-serve.ps1'
    if ((Test-Path $serve) -and (-not (Test-Port 8012))) {
        try {
            Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $serve -WindowStyle Hidden
        } catch { Write-Warning "Memory sidecar did not start; continuing without memory." }
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
