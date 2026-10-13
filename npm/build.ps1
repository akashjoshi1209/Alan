<#
.SYNOPSIS
  Assembles the alan-agent npm tarball.

.DESCRIPTION
  Stages exactly what the package is allowed to ship into npm\.stage, then
  packs that directory. Staging is what makes `files` in package.json a
  guarantee rather than a hope: env\, models\, vendor\ and alan-research\
  live in the repo root and are never copied in, so there is no path by which
  they can reach the registry. Run `build.ps1 -DryRun` to list the tarball's
  contents without writing it.

  The staging directory is deliberately NOT the repo root. npm pack from the
  root would sweep in 8 GB of sidecar environments; from .stage it can only
  see the few megabytes we put there.
#>
[CmdletBinding()]
param(
    # List the tarball contents without creating the tarball.
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$npmDir = $PSScriptRoot
$repo   = Split-Path -Parent $npmDir
$stage  = Join-Path $npmDir '.stage'

# Fail loudly on a missing input rather than packing a partial package.
$required = @(
    (Join-Path $npmDir  'package.json'),
    (Join-Path $npmDir  'bin\alan-agent.js'),
    (Join-Path $repo    'src'),
    (Join-Path $repo    'requirements.txt'),
    (Join-Path $repo    'README.md')
)
foreach ($item in $required) {
    if (-not (Test-Path $item)) { throw "missing required input: $item" }
}

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

Copy-Item (Join-Path $npmDir 'package.json')        $stage
Copy-Item (Join-Path $npmDir 'bin')                 (Join-Path $stage 'bin') -Recurse
Copy-Item (Join-Path $repo   'src')                 (Join-Path $stage 'src') -Recurse
Copy-Item (Join-Path $repo   'requirements.txt')    $stage
Copy-Item (Join-Path $repo   'README.md')           $stage

# Every test run leaves __pycache__ behind; it must not ride along.
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' -EA SilentlyContinue |
    Remove-Item -Recurse -Force

Write-Host "staged -> $stage"

Push-Location $stage
try {
    if ($DryRun) {
        Write-Host "=== dry run: what WOULD ship ==="
        npm pack --dry-run --json
    } else {
        npm pack --json
    }
    if ($LASTEXITCODE -ne 0) { throw "npm pack failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
