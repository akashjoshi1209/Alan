#Requires -Version 5.1
<#
.SYNOPSIS
    Show which microphone carries signal, and how loud it is.

.DESCRIPTION
    Voice input stops on `peak >= [voice] threshold` (default 500). When the
    Windows *default* input device is a silent one -- a front-panel jack with
    nothing in it, or a paired Bluetooth headset in its dock -- the recorder
    records for the full max_seconds and reports "heard nothing", which looks
    exactly like a quiet room.

    This meters a device and prints its level live, so the two cases stop
    being indistinguishable. It also prints the exact `[voice] device = N`
    line to put in config\settings.toml.

    Runs under the sidecar interpreter (env\whisper\python) because it needs
    PyAudio, which lives only there.

.PARAMETER Device
    PyAudio input device index to meter. -1 (default) meters whatever Windows
    calls the default recording device.

.PARAMETER Seconds
    How long to meter each device. Default 8.

.PARAMETER All
    Meter every input device in turn and recommend the loudest one.

.PARAMETER Threshold
    The [voice] threshold to test against. Default 500.

.EXAMPLE
    .\scripts\voice-levels.ps1
    Meter the default microphone while you speak.

.EXAMPLE
    .\scripts\voice-levels.ps1 -All
    Meter every device and print the [voice] device value to use.

.EXAMPLE
    .\scripts\voice-levels.ps1 -Device 4
    Meter one specific device.
#>
[CmdletBinding()]
param(
    [int]$Device = -1,
    [double]$Seconds = 8.0,
    [switch]$All,
    [int]$Threshold = 500
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "env\whisper\python\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "sidecar interpreter not found at $py - run scripts\setup-whisper.ps1 first"
    exit 1
}

$probe = Join-Path $PSScriptRoot "voice_levels.py"

$argv = @($probe, "--device", $Device, "--seconds", $Seconds, "--threshold", $Threshold)
if ($All) { $argv += "--all" }

& $py @argv
exit $LASTEXITCODE
