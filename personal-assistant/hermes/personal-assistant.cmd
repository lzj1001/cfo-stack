@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERMES_HOME%\bin\personal-assistant.ps1"
exit /b %ERRORLEVEL%
