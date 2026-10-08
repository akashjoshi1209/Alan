# Pull (download) a local model into the USB's models folder via the bundled Ollama.
# Usage:  scripts\pull-model.ps1                 -> pulls qwen2.5:1.5b (default)
#         scripts\pull-model.ps1 qwen2.5:3b       -> pulls a different model
param([string]$Model = 'qwen2.5:1.5b')

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root 'env\ollama\ollama.exe'
$ollamaPort = 11435

if (-not (Test-Path $exe)) {
    Write-Error "Ollama not found at $exe. Re-run the setup."
    exit 1
}

# Keep both the server and the client on Alan's own port + models folder.
$env:OLLAMA_MODELS = (Join-Path $root 'models')
$env:OLLAMA_HOST   = "127.0.0.1:$ollamaPort"

function Test-Ollama {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$ollamaPort/api/tags" -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

if (-not (Test-Ollama)) {
    Write-Host "Starting Ollama server (models: $env:OLLAMA_MODELS) ..." -ForegroundColor Cyan
    Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Ollama) { break }
    }
}
if (-not (Test-Ollama)) { Write-Error "Ollama server did not start on port $ollamaPort."; exit 1 }

Write-Host "Pulling model '$Model' (this can take a while) ..." -ForegroundColor Cyan
& $exe pull $Model
Write-Host "Done. Models are stored under: $env:OLLAMA_MODELS" -ForegroundColor Green
