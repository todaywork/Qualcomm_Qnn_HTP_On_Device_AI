@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

set "RUNNER=%~dp0run_genie_cli.py"
set "DEFAULT_INPUT=%~dp0smoke20_input.xlsx"
set "INPUT_FILE="

if not exist "%RUNNER%" (
    echo ERROR: Missing %RUNNER%
    echo.
    pause
    exit /b 1
)

rem A dragged .xlsx path is batch input; any other argument is forwarded.
if "%~1"=="" goto :interactive
if /i "%~x1"==".xlsx" (
    set "INPUT_FILE=%~f1"
    goto :run_batch
)
goto :run_advanced

:interactive
echo ============================================================
echo Qwen3-0.6B Genie CLI batch inference
echo ============================================================
echo Drag an .xlsx input file into this window, then press Enter.
echo Or press Enter directly to use:
echo   %DEFAULT_INPUT%
echo.
set /p "INPUT_FILE=Excel file: "

if not defined INPUT_FILE set "INPUT_FILE=%DEFAULT_INPUT%"
set "INPUT_FILE=%INPUT_FILE:"=%"
for %%I in ("%INPUT_FILE%") do set "INPUT_FILE=%%~fI"

:run_batch
if /i not "%INPUT_FILE:~-5%"==".xlsx" (
    echo ERROR: Input file must use the .xlsx extension:
    echo   %INPUT_FILE%
    set "RUN_EXIT=2"
    goto :finished
)
if not exist "%INPUT_FILE%" (
    echo ERROR: Input workbook does not exist:
    echo   %INPUT_FILE%
    set "RUN_EXIT=2"
    goto :finished
)

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: python was not found in PATH.
    set "RUN_EXIT=3"
    goto :finished
)
python -c "import openpyxl, tokenizers" >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python package openpyxl or tokenizers is missing.
    echo Install with: python -m pip install openpyxl tokenizers
    set "RUN_EXIT=3"
    goto :finished
)

python "%RUNNER%" --mode batch --input "%INPUT_FILE%"
set "RUN_EXIT=%ERRORLEVEL%"
goto :finished

:run_advanced
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: python was not found in PATH.
    set "RUN_EXIT=3"
    goto :finished
)
python -c "import openpyxl, tokenizers" >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python package openpyxl or tokenizers is missing.
    echo Install with: python -m pip install openpyxl tokenizers
    set "RUN_EXIT=3"
    goto :finished
)
python "%RUNNER%" %*
set "RUN_EXIT=%ERRORLEVEL%"

:finished
echo.
if not "%RUN_EXIT%"=="0" (
    echo genie_cli deployment or inference failed. Exit code: %RUN_EXIT%
) else (
    echo genie_cli deployment and inference completed successfully.
)

echo.
pause
exit /b %RUN_EXIT%
