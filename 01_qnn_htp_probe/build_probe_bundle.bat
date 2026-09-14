@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "QNN_BUILDER_SELF=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$text=[IO.File]::ReadAllText($env:QNN_BUILDER_SELF); $marker='# POWERSHELL_PAYLOAD'; & ([scriptblock]::Create($text.Substring($text.LastIndexOf($marker)+$marker.Length)))"
set "BUILD_RC=%ERRORLEVEL%"
pause
exit /b %BUILD_RC%
# POWERSHELL_PAYLOAD
$ErrorActionPreference = 'Stop'
try {
$builderRoot = Split-Path -Parent $env:QNN_BUILDER_SELF
$defaultSdk = 'E:\QualComm\AIStack\QAIRT\2.46.0.260424'
$defaultArch = '75'
$runStamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
function Get-DefaultOutput([string]$arch) {
    $candidate = Join-Path $builderRoot "HTP_V${arch}_$runStamp"
    $suffix = 1
    while (Test-Path -LiteralPath $candidate) {
        $candidate = Join-Path $builderRoot "HTP_V${arch}_${runStamp}_$suffix"
        $suffix++
    }
    return $candidate
}
function Read-Default([string]$name, [string]$defaultValue) {
    $value = (Read-Host "$name [$defaultValue]").Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($value)) { return $defaultValue }
    return $value
}
$defaultOutput = Get-DefaultOutput $defaultArch
Write-Host 'QNN Android HTP bundle builder (uses system PATH adb at probe time)'
Write-Host ''
Write-Host 'Example / defaults:'
Write-Host "  SdkRoot         = $defaultSdk"
Write-Host "  HtpArch         = $defaultArch"
Write-Host "  OutputDirectory = $defaultOutput"
Write-Host "  TargetLabel     = HTP_V$defaultArch"
Write-Host ''
Write-Host 'Press Enter at each prompt to use the value in brackets.'
Write-Host 'Press Enter four times to build with all defaults. Type a value to override it.'
Write-Host 'Default output is a new timestamped folder beside this BAT; existing bundles are preserved.'
Write-Host ''
$SdkRoot = Read-Default 'SdkRoot' $defaultSdk
$HtpArch = Read-Default 'HtpArch (68 / 73 / 75)' $defaultArch
if ($HtpArch -notmatch '^\d{2}$') { throw 'HtpArch must be two digits, e.g. 68, 73 or 75.' }
if ($HtpArch -ne $defaultArch) { $defaultOutput = Get-DefaultOutput $HtpArch }
$OutputDirectory = Read-Default 'OutputDirectory' $defaultOutput
$TargetLabel = Read-Default 'TargetLabel' "HTP_V$HtpArch"
Write-Host ''
Write-Host 'Selected parameters:'
Write-Host "  SdkRoot         = $SdkRoot"
Write-Host "  HtpArch         = $HtpArch"
Write-Host "  OutputDirectory = $OutputDirectory"
Write-Host "  TargetLabel     = $TargetLabel"

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}
$SdkRoot = (Resolve-Path -LiteralPath $SdkRoot).Path

$destination = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $destination) {
    if (-not (Test-Path -LiteralPath $destination -PathType Container) -or
        (Get-ChildItem -LiteralPath $destination -Force | Measure-Object).Count -ne 0) {
        throw 'OutputDirectory must be new or empty. Build a separate bundle to avoid mixing SDK versions.'
    }
}
if (-not $TargetLabel) { $TargetLabel = Split-Path $destination -Leaf }
$metadata = Get-Content -LiteralPath (Join-Path $SdkRoot 'sdk.yaml') -Raw
$version = [regex]::Match($metadata, '(?m)^version:\s*([^\r\n]+)').Groups[1].Value.Trim()
$buildId = [regex]::Match($metadata, '(?m)^build_id:\s*([^\r\n]+)').Groups[1].Value.Trim()
if (-not $version -or -not $buildId) { throw 'sdk.yaml must identify version and build_id' }
$batchTemplate = Join-Path $builderRoot 'SA8295_V68/check_htp_adb.bat'
$verifyTemplate = Join-Path $builderRoot 'SA8295_V68/verify_bundle.ps1'
foreach ($path in @($batchTemplate, $verifyTemplate)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing script template: $path" }
}
$required = @('bin/aarch64-android/qnn-platform-validator', 'lib/aarch64-android/libQnnHtp.so',
    "lib/aarch64-android/libQnnHtpV${HtpArch}Stub.so", "lib/aarch64-android/libQnnHtpV${HtpArch}CalculatorStub.so",
    "lib/hexagon-v$HtpArch/unsigned/libQnnHtpV${HtpArch}Skel.so", "lib/hexagon-v$HtpArch/unsigned/libCalculator_skel.so")
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $SdkRoot $relative) -PathType Leaf)) {
        throw "This SDK cannot supply the requested Android V$HtpArch bundle; missing: $relative"
    }
}
$resources = @([pscustomobject]@{Source=(Join-Path $SdkRoot $required[0]);Target='runtime/bin/qnn-platform-validator';Origin=$required[0]})
$hostNames = @('libQnnHtp.so', 'libQnnHtpPrepare.so', 'libQnnSystem.so', 'libQnnCpu.so',
    'libQnnModelDlc.so', 'libQnnSaver.so', 'libQnnHtpNetRunExtensions.so', 'libQnnHtpProfilingReader.so',
    'libQnnHtpOptraceProfilingReader.so', 'libQnnChrometraceProfilingReader.so',
    "libQnnHtpV${HtpArch}Stub.so", "libQnnHtpV${HtpArch}CalculatorStub.so")
foreach ($name in $hostNames) {
    $relative = "lib/aarch64-android/$name"
    $source = Join-Path $SdkRoot $relative
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        $resources += [pscustomobject]@{Source=$source;Target="runtime/lib/$name";Origin=$relative}
    }
}
foreach ($item in (Get-ChildItem -LiteralPath (Join-Path $SdkRoot "lib/hexagon-v$HtpArch/unsigned") -File)) {
    $resources += [pscustomobject]@{Source=$item.FullName;Target="runtime/dsp/$($item.Name)";Origin="lib/hexagon-v$HtpArch/unsigned/$($item.Name)"}
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$records = foreach ($resource in $resources) {
    $target = Join-Path $destination $resource.Target
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $resource.Source -Destination $target
    $hash = Get-Sha256 $target
    if ($hash -ne (Get-Sha256 $resource.Source)) { throw "Copy hash mismatch: $target" }
    [pscustomobject]@{path=$resource.Target;size_bytes=(Get-Item -LiteralPath $target).Length;sha256=$hash;source=$resource.Origin}
}
$batch = Get-Content -LiteralPath $batchTemplate -Raw
$batch = [regex]::Replace($batch, 'set "HTP_ARCH=\d+"', ('set "HTP_ARCH=' + $HtpArch + '"'))
[IO.File]::WriteAllText((Join-Path $destination 'check_htp_adb.bat'), ($batch -replace '\r?\n', "`r`n"), [Text.ASCIIEncoding]::new())
Copy-Item -LiteralPath $verifyTemplate -Destination (Join-Path $destination 'verify_bundle.ps1')
$manifest = [ordered]@{schema_version=1;target_label=$TargetLabel;htp_architecture=[int]$HtpArch;
    platform='aarch64-android';adb_source='system_PATH';sdk_product='QAIRT';sdk_version=$version;sdk_build_id=$buildId;
    sdk_directory=(Split-Path $SdkRoot -Leaf);source_sdk_root=$SdkRoot;created_at=(Get-Date -Format o);
    device_validation="NOT_VERIFIED_ON_V$HtpArch";files=@($records)}
[IO.File]::WriteAllText((Join-Path $destination 'bundle_manifest.json'), ($manifest | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
Write-Output "[BUILT] $destination (HTP V$HtpArch, QAIRT $version build $buildId; $($records.Count) files)"
& (Join-Path $destination 'verify_bundle.ps1')

} catch { Write-Host ('[FAIL] ' + $_.Exception.Message); exit 1 }
