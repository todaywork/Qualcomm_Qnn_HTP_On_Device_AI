@echo off
setlocal
chcp 65001 >nul
"%~dp0tools\python\python.exe" -X utf8 "%~dp0run_probe.py" %*
exit /b %ERRORLEVEL%
