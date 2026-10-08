# Open a shell with Alan's portable environment activated.
# Then you can run things like:  python -m alan chat
$root = Split-Path -Parent $PSScriptRoot
$pyDir = Join-Path $root 'env\python'

$env:ALAN_HOME  = $root
$env:PYTHONPATH = (Join-Path $root 'src')
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OLLAMA_MODELS = (Join-Path $root 'models')
$env:OLLAMA_HOST   = '127.0.0.1:11435'
$env:PATH = $pyDir + ';' + (Join-Path $pyDir 'Scripts') + ';' + $env:PATH

Write-Host "Alan environment active." -ForegroundColor Cyan
Write-Host "  ALAN_HOME = $env:ALAN_HOME"
Write-Host "  python    = $(Join-Path $pyDir 'python.exe')"
Write-Host "  ollama    = $env:OLLAMA_HOST  (models: $env:OLLAMA_MODELS)"
Write-Host "Try:  python -m alan chat"
