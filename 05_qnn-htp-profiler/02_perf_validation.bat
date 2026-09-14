@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
python "%~dp0launch_perf_validation.py" %*
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"==9009 pause
exit /b %RESULT%