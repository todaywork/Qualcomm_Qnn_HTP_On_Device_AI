@echo off
setlocal
chcp 65001 >nul
python -X utf8 -B "%~dp0build_model_assets.py" %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %RC%
