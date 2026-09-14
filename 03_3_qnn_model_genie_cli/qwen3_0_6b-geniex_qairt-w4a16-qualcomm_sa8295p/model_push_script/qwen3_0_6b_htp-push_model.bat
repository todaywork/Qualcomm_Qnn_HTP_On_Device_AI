@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title Qwen3-0.6B-HTP Model Resource Push Tool

rem ============================================================================
rem USER CONFIGURATION - edit only the default local path when needed.
rem Keep the set "NAME=value" syntax and the surrounding double quotes.
rem ============================================================================
set "DEFAULT_LOCAL_MODEL_DIR=E:\QualComm\ai-hub-compiles\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p-office1"

rem Fixed device destination. The script does not prompt the user to change it.
set "REMOTE_MODEL_DIR=/data/local/tmp/genie_qwen3_quality"
set "ADB_EXE=adb"
set "ADB_SERIAL="

set "LOCAL_MODEL_DIR=%DEFAULT_LOCAL_MODEL_DIR%"

echo ============================================================================
echo Select the local model resource folder.
echo Drag a folder into this window and press Enter to use it.
echo Press Enter directly to use the default folder:
echo "%DEFAULT_LOCAL_MODEL_DIR%"
echo ============================================================================
echo.

rem The first command-line argument also overrides the local folder.
if not "%~1"=="" goto :use_argument

set "USER_LOCAL_MODEL_DIR="
set /p "USER_LOCAL_MODEL_DIR=Local model folder: "
if defined USER_LOCAL_MODEL_DIR set "LOCAL_MODEL_DIR=%USER_LOCAL_MODEL_DIR%"
goto :normalize_local_path

:use_argument
set "LOCAL_MODEL_DIR=%~1"

:normalize_local_path

rem Remove quotes added automatically by Windows drag-and-drop and resolve the path.
for %%I in ("%LOCAL_MODEL_DIR:"=%") do set "LOCAL_MODEL_DIR=%%~fI"

echo.

echo ============================================================================
echo Qwen3-0.6B-HTP Model Resource Push Tool
echo Local directory : "%LOCAL_MODEL_DIR%"
echo Device directory: "%REMOTE_MODEL_DIR%"
if defined ADB_SERIAL echo ADB device      : "%ADB_SERIAL%"
echo ============================================================================
echo.

if not defined LOCAL_MODEL_DIR (
    echo [ERROR] LOCAL_MODEL_DIR is empty.
    goto :failed
)

if not exist "%LOCAL_MODEL_DIR%\" (
    echo [ERROR] Local model directory does not exist:
    echo         "%LOCAL_MODEL_DIR%"
    goto :failed
)

if not defined REMOTE_MODEL_DIR (
    echo [ERROR] REMOTE_MODEL_DIR is empty.
    goto :failed
)

rem Reject broad targets because this script deletes REMOTE_MODEL_DIR recursively.
if "%REMOTE_MODEL_DIR%"=="/" goto :unsafe_remote
if "%REMOTE_MODEL_DIR%"=="/data" goto :unsafe_remote
if "%REMOTE_MODEL_DIR%"=="/data/" goto :unsafe_remote
if "%REMOTE_MODEL_DIR%"=="/data/local" goto :unsafe_remote
if "%REMOTE_MODEL_DIR%"=="/data/local/" goto :unsafe_remote
if "%REMOTE_MODEL_DIR%"=="/data/local/tmp" goto :unsafe_remote
if "%REMOTE_MODEL_DIR%"=="/data/local/tmp/" goto :unsafe_remote

if exist "%ADB_EXE%" goto :adb_found
where "%ADB_EXE%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] adb was not found. Set ADB_EXE above or add platform-tools to PATH.
    goto :failed
)

:adb_found
call :run_adb get-state 1>nul 2>nul
if errorlevel 1 (
    echo [ERROR] No usable Android device was found.
    call :run_adb devices
    goto :failed
)

echo [1/4] Stopping com.qairt.qwen3htp...
call :run_adb shell am force-stop com.qairt.qwen3htp

echo [2/4] Removing old resources from the device...
call :run_adb shell "rm -rf -- '%REMOTE_MODEL_DIR%'"
if errorlevel 1 goto :adb_failed

echo [3/4] Pushing model resources to the device...
call :run_adb push "%LOCAL_MODEL_DIR%" "%REMOTE_MODEL_DIR%"
if errorlevel 1 goto :adb_failed

echo [4/4] Updating permissions...
call :run_adb shell "chmod -R 777 -- '%REMOTE_MODEL_DIR%'"
if errorlevel 1 goto :adb_failed

echo.
echo [SUCCESS] Model resources were pushed to:
echo           "%REMOTE_MODEL_DIR%"
echo.
call :run_adb shell "ls -lah '%REMOTE_MODEL_DIR%'"
echo.
pause
exit /b 0

:unsafe_remote
echo [ERROR] Refusing to recursively delete an unsafe device directory:
echo         "%REMOTE_MODEL_DIR%"
goto :failed

:adb_failed
echo.
echo [ERROR] An adb operation failed. Review the output above.

:failed
echo.
pause
exit /b 1

:run_adb
if defined ADB_SERIAL (
    "%ADB_EXE%" -s "%ADB_SERIAL%" %*
) else (
    "%ADB_EXE%" %*
)
exit /b %errorlevel%
