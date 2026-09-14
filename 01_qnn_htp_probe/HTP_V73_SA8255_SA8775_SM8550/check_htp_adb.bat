@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Portable Android HTP probe. Runtime files must match HTP_ARCH and one SDK build.
set "HTP_ARCH=73"
set "RC=1"
set "BASE=%~dp0"
set "STAGING=%BASE%runtime"
set "ADB="
set "ADB_ARGS="
set "NO_PAUSE="
if /i "%~1"=="--help" goto help
if /i "%~1"=="--check-files" goto check_files
if /i "%~1"=="--no-pause" (set "NO_PAUSE=1") else if not "%~1"=="" (set ADB_ARGS=-s "%~1")
if /i "%~2"=="--no-pause" set "NO_PAUSE=1"
for %%A in (adb.exe) do set "ADB=%%~$PATH:A"
if not defined ADB (
  echo [FAIL] adb.exe was not found in system PATH. Install Android platform-tools or update PATH.
  goto finish
)
for %%F in (bin\qnn-platform-validator lib\libQnnHtp.so lib\libQnnHtpV%HTP_ARCH%Stub.so lib\libQnnHtpV%HTP_ARCH%CalculatorStub.so dsp\libQnnHtpV%HTP_ARCH%Skel.so dsp\libCalculator_skel.so) do (
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
if exist "%BASE%bundle_manifest.json" copy /y "%BASE%bundle_manifest.json" "%LOGDIR%\bundle_manifest.json" >nul
>"%LOGDIR%\result.txt" echo expected_architecture=V%HTP_ARCH%
>>"%LOGDIR%\result.txt" echo probe_started=true
echo [1/4] Checking ADB device...
"%ADB%" devices -l >"%LOGDIR%\devices.txt" 2>&1
"%ADB%" %ADB_ARGS% get-state >"%LOGDIR%\connection.txt" 2>&1
if errorlevel 1 (
  type "%LOGDIR%\connection.txt"
  type "%LOGDIR%\devices.txt"
  echo [FAIL] Connect and authorize one device, or specify its serial number.
  goto finish
)
set "SERIAL="
"%ADB%" %ADB_ARGS% get-serialno >"%LOGDIR%\serial.txt" 2>"%LOGDIR%\serial_error.txt"
if errorlevel 1 goto finish
for /f "usebackq delims=" %%S in ("%LOGDIR%\serial.txt") do set "SERIAL=%%S"
if not defined SERIAL goto finish
set ADB_ARGS=-s "%SERIAL%"
>>"%LOGDIR%\result.txt" echo device_serial=%SERIAL%
>>"%LOGDIR%\result.txt" echo remote_directory=%REMOTE%
"%ADB%" %ADB_ARGS% shell "getprop ro.product.model; getprop ro.soc.model; getprop ro.board.platform; getprop ro.product.cpu.abi; getenforce; id" >"%LOGDIR%\device_info.txt" 2>&1
if errorlevel 1 goto finish
type "%LOGDIR%\device_info.txt"
findstr /l /c:"arm64-v8a" "%LOGDIR%\device_info.txt" >nul
if errorlevel 1 (
  echo [FAIL] This bundle requires an arm64 Android device.
  goto finish
)
echo [2/4] Deploying HTP V%HTP_ARCH% runtime: cleaning and recreating the fixed device directory...
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
echo [3/4] Checking detected DSP architecture before the unit test...
"%ADB%" %ADB_ARGS% shell "cd %REMOTE% && export LD_LIBRARY_PATH=%REMOTE%/lib && export ADSP_LIBRARY_PATH='%REMOTE%/dsp;/vendor/lib/rfsa/adsp;/vendor/dsp;/dsp' && ./bin/qnn-platform-validator --backend dsp --coreVersion --debug --targetPath %REMOTE%/validator_output" >"%LOGDIR%\core_version.txt" 2>&1
set "CORE_RC=%ERRORLEVEL%"
type "%LOGDIR%\core_version.txt"
>>"%LOGDIR%\result.txt" echo core_query_exit_code=%CORE_RC%
if not "%CORE_RC%"=="0" goto core_failed
findstr /i /r /c:"Hexagon Architecture V%HTP_ARCH%\>" "%LOGDIR%\core_version.txt" >nul
if errorlevel 1 goto core_failed
echo Running qnn-platform-validator DSP unit test...
"%ADB%" %ADB_ARGS% shell "cd %REMOTE% && export LD_LIBRARY_PATH=%REMOTE%/lib && export ADSP_LIBRARY_PATH='%REMOTE%/dsp;/vendor/lib/rfsa/adsp;/vendor/dsp;/dsp' && ./bin/qnn-platform-validator --backend dsp --libVersion --coreVersion --testBackend --debug --targetPath %REMOTE%/validator_output" >"%LOGDIR%\validator.txt" 2>&1
set "VALIDATOR_RC=%ERRORLEVEL%"
type "%LOGDIR%\validator.txt"
echo [4/4] Collecting validator reports...
"%ADB%" %ADB_ARGS% pull "%REMOTE%/validator_output" "%LOGDIR%\validator_output" >"%LOGDIR%\pull.txt" 2>&1
if errorlevel 1 echo [WARN] Report download failed. See pull.txt; console log is saved.
>>"%LOGDIR%\result.txt" echo validator_exit_code=%VALIDATOR_RC%
if not "%VALIDATOR_RC%"=="0" goto validator_failed
findstr /i /c:"Unit Test on the backend DSP: Passed" "%LOGDIR%\validator.txt" >nul
if errorlevel 1 goto validator_failed
findstr /i /r /c:"Hexagon Architecture V%HTP_ARCH%\>" "%LOGDIR%\validator.txt" >nul
if errorlevel 1 goto validator_failed
set "RC=0"
>>"%LOGDIR%\result.txt" echo result=PASS
echo [PASS] HTP V%HTP_ARCH% DSP platform unit test passed under ADB shell.
echo This does not validate APK permissions or model inference.
goto finish

:core_failed
>>"%LOGDIR%\result.txt" echo result=ARCH_MISMATCH_OR_UNCONFIRMED
echo [FAIL] Expected Hexagon V%HTP_ARCH%; architecture query failed or did not match.
echo Unit test was not run. See core_version.txt.
goto finish

:validator_failed
>>"%LOGDIR%\result.txt" echo result=FAIL_OR_UNCONFIRMED
echo [FAIL] Validator failed, architecture mismatched, or DSP pass marker was absent.
goto finish

:check_files
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%BASE%verify_bundle.ps1"
exit /b %ERRORLEVEL%

:help
echo Usage: check_htp_adb.bat [device_serial] [--no-pause]
echo        check_htp_adb.bat --check-files
echo Uses bundled Android runtime and system PATH adb. HTP architecture: V%HTP_ARCH%.
echo QAIRT version and source hashes: bundle_manifest.json.
echo --check-files validates local files only; it does not access a device.
echo Example: check_htp_adb.bat DEVICE_SERIAL --no-pause
exit /b 0

:finish
if defined LOGDIR if not "%RC%"=="0" (
  findstr /b /c:"result=" "%LOGDIR%\result.txt" >nul 2>&1
  if errorlevel 1 >>"%LOGDIR%\result.txt" echo result=INCOMPLETE
)
if defined LOGDIR echo Logs: "%LOGDIR%"
if defined REMOTE echo Device files retained at: %REMOTE%
if not "%RC%"=="0" echo [FAIL] Probe did not complete successfully. See the logs above.
if not defined NO_PAUSE pause
exit /b %RC%
