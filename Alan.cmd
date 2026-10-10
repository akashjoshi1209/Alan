@echo off
rem Alan's launcher -- chat without the PowerShell tax.
rem
rem This used to hand off to scripts\run.ps1, which meant every `alan` paid
rem ~0.5s for a PowerShell process plus its sidecar bookkeeping before Python
rem even started. The chat prompt should draw in the next moment, not the
rem next few seconds, so the fast door is cmd itself: set the environment,
rem run Python, exit. scripts\run.ps1 remains the full launcher -- visible
rem sidecar lifecycle, one-shot commands -- and `voice` still routes through
rem it, because that is the one flow that must *wait* (the speech model
rem loads before the first word can be read).
setlocal

set "ALAN_HOME=%~dp0"
if "%ALAN_HOME:~-1%"=="\" set "ALAN_HOME=%ALAN_HOME:~0,-1%"

rem Self-contained environment: nothing is read from a host Python install,
rem and the bundled Ollama keeps its own port (11435) so it cannot collide
rem with a host-installed one on 11434.
set "PYTHONPATH=%ALAN_HOME%\src"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "OLLAMA_MODELS=%ALAN_HOME%\models"
set "OLLAMA_HOST=127.0.0.1:11435"
set "PATH=%ALAN_HOME%\env\python;%ALAN_HOME%\env\python\Scripts;%PATH%"

if /i "%~1"=="voice" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%ALAN_HOME%\scripts\run.ps1" %*
    exit /b %ERRORLEVEL%
)

rem No arguments means chat, the same default run.ps1 has always given a
rem bare launch -- and the one a double-click asks for.
if "%~1"=="" (
    "%ALAN_HOME%\env\python\python.exe" -m alan chat
) else (
    "%ALAN_HOME%\env\python\python.exe" -m alan %*
)
exit /b %ERRORLEVEL%
