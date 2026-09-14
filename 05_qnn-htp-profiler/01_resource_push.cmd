@echo off
rem 01 resource push: pick a model under models\, then deploy + export context + generate inputs
setlocal EnableDelayedExpansion
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo Available models:
set n=0
for /d %%D in ("models\*") do (
    if exist "%%~fD\profile-config.json" (
        set /a n+=1
        set "CFG!n!=%%~fD\profile-config.json"
        echo   !n!. %%~nxD
    )
)

if !n!==0 (
    echo [ERROR] No model config found: models\*\profile-config.json
    exit /b 1
)
if !n!==1 (
    set "CHOICE=1"
    echo Only one model found, auto-selected.
) else (
    set /p CHOICE=Select model number [1-!n!]: 
)

set "CONFIG=!CFG%CHOICE%!"
if not defined CONFIG (
    echo [ERROR] Invalid choice: %CHOICE%
    exit /b 1
)

echo Using config: !CONFIG!
echo.
python 01_resource_push.py --config "!CONFIG!" %*
endlocal
