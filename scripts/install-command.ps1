# Alan - make `alan` runnable from any command prompt
# ---------------------------------------------------------------------------
# Installs two things, so that typing `alan` in any cmd or PowerShell window
# starts the assistant:
#
#   1. A forwarder at <shimDir>\alan.cmd. This is the one that actually runs.
#   2. This folder on your *user* PATH, as a second, independent route.
#
# WHY BOTH, WHEN ONE WOULD DO
# They fail differently, and that is the point.
#
#   The PATH entry is the route with no moving parts: `alan` resolves straight
#   to Alan.cmd, which is drive-relative (%~dp0) and so survives the folder
#   moving to another drive. But it also puts all ~45,000 files of the product
#   on PATH, so any top-level .cmd/.exe/.bat added later becomes callable by
#   bare name from anywhere on the machine.
#
#   The forwarder is the narrow route. It exposes exactly one command, it can
#   be removed without touching PATH, and it honours %ALAN_HOME% -- so if Alan
#   moves, `alan` can follow without editing anything.
#
# The forwarder is written to a directory *earlier* in PATH than this folder,
# so it wins. Which one runs is therefore not accidental; -Status reports it.
#
# Usage:
#   scripts\install-command.ps1            -> install (idempotent)
#   scripts\install-command.ps1 -Status    -> report what is installed
#   scripts\install-command.ps1 -Uninstall -> remove both, restore PATH exactly
param(
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$root    = Split-Path -Parent $PSScriptRoot          # <Alan root>
$shimDir = Join-Path $env:USERPROFILE 'bin'
$shim    = Join-Path $shimDir 'alan.cmd'
# Derived from this script's own location, never hard-coded: running the
# installer from wherever Alan currently lives is what makes it work after a
# move to another drive. A hard-coded letter would be correct exactly once.
$fallback = $root

function Get-PathEntries {
    param([string]$Scope)
    $raw = [Environment]::GetEnvironmentVariable('Path', $Scope)
    if ($null -eq $raw) { return @() }
    return @($raw -split ';' | Where-Object { $_ -ne '' })
}

function Set-PathEntries {
    param([string[]]$Entries)
    [Environment]::SetEnvironmentVariable('Path', ($Entries -join ';'), 'User')
}

# Case-insensitive compare: Windows paths are not case-sensitive, so `d:\alan`
# and `D:\Alan` are the same folder and must not both end up on PATH.
function Test-EntryPresent {
    param([string[]]$Entries, [string]$Want)
    return [bool]($Entries | Where-Object { $_.TrimEnd('\') -ieq $Want.TrimEnd('\') })
}

# --- Status -------------------------------------------------------------------
if ($Status) {
    $entries = Get-PathEntries 'User'
    $onPath  = Test-EntryPresent $entries $root
    Write-Host "Alan folder : $root"
    Write-Host "  PATH entry     : $(if ($onPath) { 'present' } else { 'absent' })"
    Write-Host "  forwarder      : $(if (Test-Path $shim) { "present ($shim)" } else { 'absent' })"
    $alanHome = [Environment]::GetEnvironmentVariable('ALAN_HOME', 'User')
    Write-Host "  ALAN_HOME (user): $(if ($alanHome) { $alanHome } else { 'not set' })"
    if (Test-Path $shim) {
        # Which one cmd picks: PATH order decides, and the forwarder is written
        # to sit earlier. Report it rather than leave it to be inferred.
        $all = @(Get-PathEntries 'Machine') + @(Get-PathEntries 'User')
        $shimPos = -1; $rootPos = -1
        for ($i = 0; $i -lt $all.Count; $i++) {
            if ($shimPos -lt 0 -and $all[$i].TrimEnd('\') -ieq $shimDir.TrimEnd('\')) { $shimPos = $i }
            if ($rootPos -lt 0 -and $all[$i].TrimEnd('\') -ieq $root.TrimEnd('\')) { $rootPos = $i }
        }
        $winner = if ($shimPos -ge 0 -and ($rootPos -lt 0 -or $shimPos -lt $rootPos)) { 'forwarder' } else { 'PATH entry' }
        Write-Host "  ``alan`` resolves via : $winner"
    }
    exit 0
}

# --- Uninstall ----------------------------------------------------------------
if ($Uninstall) {
    Write-Host 'Removing the `alan` command ...' -ForegroundColor Cyan

    if (Test-Path $shim) {
        Remove-Item $shim -Force
        Write-Host "  removed forwarder $shim" -ForegroundColor Green
    } else {
        Write-Host "  forwarder already absent" -ForegroundColor DarkGray
    }

    $entries = Get-PathEntries 'User'
    if (Test-EntryPresent $entries $root) {
        # Rebuild the list without our entry rather than string-replacing it,
        # so nothing else in PATH is disturbed -- not even its ordering.
        $kept = @($entries | Where-Object { $_.TrimEnd('\') -ine $root.TrimEnd('\') })
        Set-PathEntries $kept
        Write-Host "  removed $root from user PATH ($($entries.Count) -> $($kept.Count) entries)" -ForegroundColor Green
    } else {
        Write-Host "  PATH entry already absent" -ForegroundColor DarkGray
    }

    # ALAN_HOME is only cleared if it still points at us, so a value the user
    # set for some other reason is never destroyed.
    $alanHome = [Environment]::GetEnvironmentVariable('ALAN_HOME', 'User')
    if ($alanHome -and $alanHome.TrimEnd('\') -ieq $root.TrimEnd('\')) {
        [Environment]::SetEnvironmentVariable('ALAN_HOME', $null, 'User')
        Write-Host "  cleared ALAN_HOME" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Done. Open a new command prompt to pick up the change." -ForegroundColor Cyan
    exit 0
}

# --- Install ------------------------------------------------------------------
Write-Host 'Installing the `alan` command ...' -ForegroundColor Cyan

# 1. The forwarder.
New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
$shimBody = @"
@echo off
rem Generated by scripts\install-command.ps1 -- edit that script, not this file.
rem
rem Forwards to Alan's own drive-relative launcher so ``alan`` works from any
rem command prompt. Resolution order:
rem   1. %ALAN_HOME%, if set  -> lets the folder move without editing this file
rem   2. $fallback            -> this folder's location when it was installed
setlocal
if defined ALAN_HOME (
    set "ALAN_TARGET=%ALAN_HOME%"
) else (
    set "ALAN_TARGET=$fallback"
)
rem cmd keeps a trailing space (from `set X=Y && ...`) or backslash, and either
rem breaks the lookup. Strip them; leave interior spaces alone.
:alan_trim
if not "%ALAN_TARGET:~-1%"==" " goto alan_trim_done
set "ALAN_TARGET=%ALAN_TARGET:~0,-1%"
goto alan_trim
:alan_trim_done
if not "%ALAN_TARGET:~-1%"=="\" goto alan_ready
set "ALAN_TARGET=%ALAN_TARGET:~0,-1%"
:alan_ready
if not exist "%ALAN_TARGET%\Alan.cmd" (
    echo alan: cannot find Alan at "%ALAN_TARGET%".
    echo        Set ALAN_HOME to the folder holding Alan.cmd, e.g.  set ALAN_HOME=X:\Alan
    echo        Or run  scripts\install-command.ps1  from the new location.
    exit /b 1
)
call "%ALAN_TARGET%\Alan.cmd" %*
exit /b %ERRORLEVEL%
"@
# Write ASCII so the file is safe regardless of the console's code page.
[System.IO.File]::WriteAllText($shim, $shimBody, [System.Text.Encoding]::ASCII)
Write-Host "  forwarder  -> $shim" -ForegroundColor Green

# 2. The PATH entry, appended so existing order is preserved.
$entries = Get-PathEntries 'User'
if (Test-EntryPresent $entries $root) {
    Write-Host "  PATH entry -> already present" -ForegroundColor DarkGray
} else {
    Set-PathEntries ($entries + $root)
    Write-Host "  PATH entry -> added $root" -ForegroundColor Green
}

# 3. Keep ALAN_HOME in step with where Alan actually is. Without this, moving
#    the folder to another drive breaks `alan` with a confusing error instead of
#    a clear one, because the forwarder would fall back to the old letter.
[Environment]::SetEnvironmentVariable('ALAN_HOME', $root, 'User')
Write-Host "  ALAN_HOME  -> $root" -ForegroundColor Green

Write-Host ""
Write-Host "Done. Open a NEW command prompt (PATH is read at launch), then try:" -ForegroundColor Cyan
Write-Host "  alan          start a chat"
Write-Host "  alan ask ""...""  one question"
Write-Host "  alan doctor   check every part of the install"
Write-Host ""
Write-Host "To undo:  scripts\install-command.ps1 -Uninstall" -ForegroundColor DarkGray
exit 0
