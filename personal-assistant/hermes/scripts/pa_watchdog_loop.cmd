@echo off
set "HERMES_HOME=%~dp0.."
set "PA_WATCHDOG_LOOP=1"
"%~dp0..\..\app\venv\Scripts\python.exe" "%~dp0pa_watchdog.py" >> "%HERMES_HOME%\logs\pa-watchdog.log" 2>&1
exit /b %ERRORLEVEL%
