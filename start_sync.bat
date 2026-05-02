@echo off
:: =============================================================================
::  start_sync.bat  —  Run DB Sync manually (no service needed)
::  Double-click this file or run it from Command Prompt
:: =============================================================================
::
::  USAGE:
::    start_sync.bat --push            send local data to remote
::    start_sync.bat --pull            receive remote data to local
::    start_sync.bat --push --watch 30 push every 30 seconds
::    start_sync.bat --pull --watch 60 pull every 60 seconds
::
:: =============================================================================

cd /d "%~dp0"
call venv\Scripts\activate.bat

if "%~1"=="" (
    echo.
    echo   ERROR: You must choose a direction.
    echo.
    echo   Send local data to remote server:
    echo     start_sync.bat --push
    echo.
    echo   Receive remote data to this machine:
    echo     start_sync.bat --pull
    echo.
    pause
    exit /b 1
)

python sync.py %*
