@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Portable HTP V73 probe using bundled QAIRT runtime and ADB.
rem Usage: check_htp_adb.bat [device_serial] [--no-pause]
if /i "%~1"=="--help" goto help
set "RC=1"
set "BASE=%~dp0"
set "STAGING=%BASE%runtime"
set "ADB=%BASE%tools\adb\adb.exe"
set "ADB_ARGS="
set "NO_PAUSE="
if /i "%~1"=="--no-pause" (set "NO_PAUSE=1") else if not "%~1"=="" (set ADB_ARGS=-s "%~1")
if /i "%~2"=="--no-pause" set "NO_PAUSE=1"
for %%A in (adb.exe AdbWinApi.dll AdbWinUsbApi.dll) do (
  if not exist "%BASE%tools\adb\%%A" (
    echo [FAIL] Missing bundled ADB file: "%BASE%tools\adb\%%A"
    goto finish
  )
)
for %%F in (bin\qnn-platform-validator lib\libQnnHtp.so lib\libQnnHtpV73Stub.so lib\libQnnHtpV73CalculatorStub.so dsp\libQnnHtpV73Skel.so dsp\libCalculator_skel.so) do (
  if not exist "%STAGING%\%%F" (
    echo [FAIL] Missing dependency: "%STAGING%\%%F"
    goto finish
  )
)
set "RUN_ID="
for /f %%T in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss_ffff"') do set "RUN_ID=%%T"
if not defined RUN_ID (
  echo [FAIL] Could not create a run identifier.
  goto finish
)
set "LOGDIR=%BASE%probe_logs\%RUN_ID%"
set "REMOTE=/data/local/tmp/qnn_platform_probe"
mkdir "%LOGDIR%"
if errorlevel 1 goto finish
echo [1/4] Checking ADB device...
"%ADB%" devices -l >"%LOGDIR%\devices.txt" 2>&1
"%ADB%" %ADB_ARGS% get-state >"%LOGDIR%\connection.txt" 2>&1
if errorlevel 1 (
  type "%LOGDIR%\connection.txt"
  type "%LOGDIR%\devices.txt"
  echo [FAIL] Connect and authorize one device, or specify its serial number.
  goto finish
)
rem Pin the selected transport for all subsequent commands.
set "SERIAL="
"%ADB%" %ADB_ARGS% get-serialno >"%LOGDIR%\serial.txt" 2>"%LOGDIR%\serial_error.txt"
if errorlevel 1 goto finish
for /f "usebackq delims=" %%S in ("%LOGDIR%\serial.txt") do set "SERIAL=%%S"
if not defined SERIAL goto finish
set ADB_ARGS=-s "%SERIAL%"
"%ADB%" %ADB_ARGS% shell "getprop ro.product.model; getprop ro.soc.model; getprop ro.board.platform; getprop ro.product.cpu.abi; getenforce; id" >"%LOGDIR%\device_info.txt" 2>&1
if errorlevel 1 goto finish
type "%LOGDIR%\device_info.txt"
findstr /l /c:"arm64-v8a" "%LOGDIR%\device_info.txt" >nul
if errorlevel 1 (
  echo [FAIL] This bundle requires an arm64 Android device.
  goto finish
)
echo [2/4] Deploying existing V73 runtime bundle...
rem Refuse deletion unless both the configured and device-resolved path are exact.
if not "%REMOTE%"=="/data/local/tmp/qnn_platform_probe" (
  echo [FAIL] Refusing to clean an unexpected remote directory.
  goto finish
)
echo Clearing fixed device directory: %REMOTE%
"%ADB%" %ADB_ARGS% shell "test ! -L /data/local/tmp/qnn_platform_probe && test $(readlink -f /data/local/tmp) = /data/local/tmp && rm -rf /data/local/tmp/qnn_platform_probe && mkdir -p /data/local/tmp/qnn_platform_probe/bin /data/local/tmp/qnn_platform_probe/lib /data/local/tmp/qnn_platform_probe/dsp /data/local/tmp/qnn_platform_probe/validator_output" >"%LOGDIR%\deploy.txt" 2>&1
if errorlevel 1 goto finish
"%ADB%" %ADB_ARGS% push "%STAGING%\bin\qnn-platform-validator" "%REMOTE%/bin/" >>"%LOGDIR%\deploy.txt" 2>&1
if errorlevel 1 goto finish
"%ADB%" %ADB_ARGS% push "%STAGING%\lib\." "%REMOTE%/lib/" >>"%LOGDIR%\deploy.txt" 2>&1
if errorlevel 1 goto finish
"%ADB%" %ADB_ARGS% push "%STAGING%\dsp\." "%REMOTE%/dsp/" >>"%LOGDIR%\deploy.txt" 2>&1
if errorlevel 1 goto finish
"%ADB%" %ADB_ARGS% shell "chmod 755 %REMOTE%/bin/qnn-platform-validator" >>"%LOGDIR%\deploy.txt" 2>&1
if errorlevel 1 goto finish
echo [3/4] Running qnn-platform-validator DSP backend test...
"%ADB%" %ADB_ARGS% shell "cd %REMOTE% && export LD_LIBRARY_PATH=%REMOTE%/lib && export ADSP_LIBRARY_PATH='%REMOTE%/dsp;/vendor/lib/rfsa/adsp;/vendor/dsp;/dsp' && ./bin/qnn-platform-validator --backend dsp --libVersion --coreVersion --testBackend --debug --targetPath %REMOTE%/validator_output" >"%LOGDIR%\validator.txt" 2>&1
set "VALIDATOR_RC=%ERRORLEVEL%"
type "%LOGDIR%\validator.txt"
echo [4/4] Collecting validator reports...
"%ADB%" %ADB_ARGS% pull "%REMOTE%/validator_output" "%LOGDIR%\validator_output" >"%LOGDIR%\pull.txt" 2>&1
if errorlevel 1 echo [WARN] Report download failed. See pull.txt; console log is saved.
>"%LOGDIR%\result.txt" echo validator_exit_code=%VALIDATOR_RC%
>>"%LOGDIR%\result.txt" echo device_serial=%SERIAL%
>>"%LOGDIR%\result.txt" echo remote_directory=%REMOTE%
if not "%VALIDATOR_RC%"=="0" goto validator_failed
rem Require the actual unit-test pass marker, not only process exit code.
findstr /i /c:"Unit Test on the backend DSP: Passed" "%LOGDIR%\validator.txt" >nul
if errorlevel 1 goto validator_failed
set "RC=0"
>>"%LOGDIR%\result.txt" echo result=PASS
echo [PASS] DSP/HTP platform unit test passed under ADB shell.
echo This does not validate APK permissions or model inference.
goto finish

:validator_failed
>>"%LOGDIR%\result.txt" echo result=FAIL_OR_UNCONFIRMED
echo [FAIL] Validator failed or the DSP unit-test pass marker was absent.
echo This bundle is for HTP V73. Check validator.txt for architecture or dependency errors.
goto finish

:help
echo Usage: check_htp_adb.bat [device_serial] [--no-pause]
echo Uses bundled runtime and tools\adb. Runtime bundle: HTP V73.
echo Example: check_htp_adb.bat AN3GUT4220011858 --no-pause
exit /b 0

:finish
if defined LOGDIR echo Logs: "%LOGDIR%"
if defined REMOTE echo Device files retained at: %REMOTE%
if not "%RC%"=="0" echo [FAIL] Probe did not complete successfully. See the logs above.
if not defined NO_PAUSE pause
exit /b %RC%
