@echo off
REM Launch Squid HCS on Windows, via the virtualenv that setup_windows.ps1 builds.
REM
REM This exists so a startup failure stays readable: a shortcut pointed straight at
REM python.exe closes its console the instant the process dies, so a bad config or a
REM missing camera driver would flash past unreadable. It is the counterpart of
REM Terminal=true in the .desktop entry setup_22.04.sh writes on Ubuntu.
REM
REM Usage:  main_hcs.bat [args...]      e.g.  main_hcs.bat --simulation

setlocal

REM control/_def.py resolves "configuration*.ini" and cache\ relative to the working
REM directory, so run from the software root no matter where this was invoked from.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: no virtualenv found at "%~dp0.venv".
    echo Run setup_windows.ps1 first:
    echo.
    echo     powershell -ExecutionPolicy Bypass -File setup_windows.ps1
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main_hcs.py %*

if errorlevel 1 (
    echo.
    echo Squid exited with error code %errorlevel%.
    pause
)

endlocal
