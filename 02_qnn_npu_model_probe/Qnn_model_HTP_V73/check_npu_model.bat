@echo off
setlocal
chcp 65001 >nul
python -X utf8 -B "%~dp0run_probe.py" %*
exit /b %ERRORLEVEL%
