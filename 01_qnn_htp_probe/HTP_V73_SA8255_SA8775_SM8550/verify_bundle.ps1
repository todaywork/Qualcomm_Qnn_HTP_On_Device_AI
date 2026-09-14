$ErrorActionPreference = 'Stop'
function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}
try {
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bundle_manifest.json') -Raw | ConvertFrom-Json
    $base = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    $failures = @()
    foreach ($entry in $manifest.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $base $entry.path))
        if (-not $path.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Manifest path is outside this bundle: $($entry.path)"
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $failures += "Missing: $($entry.path)"
        } elseif ((Get-Item -LiteralPath $path).Length -ne $entry.size_bytes -or
                  (Get-Sha256 $path) -ne $entry.sha256) {
            $failures += "Size/hash mismatch: $($entry.path)"
        }
    }
    $arch = [string]$manifest.htp_architecture
    $script = Get-Content -LiteralPath (Join-Path $base 'check_htp_adb.bat') -Raw
    if ($script -notmatch ('(?m)^set "HTP_ARCH=' + [regex]::Escape($arch) + '"\r?$')) {
        $failures += 'Batch architecture does not match the resource manifest'
    }
    $actual = Get-ChildItem -LiteralPath (Join-Path $base 'runtime') -File -Recurse
    foreach ($item in $actual) {
        $relative = $item.FullName.Substring($base.Length).Replace('\', '/')
        if ($relative -notin $manifest.files.path) { $failures += "Unlisted runtime/tool file: $relative" }
        if ($item.Name -match 'V(\d+)' -and $Matches[1] -ne $arch) {
            $failures += "Wrong-architecture file: $relative"
        }
    }
    if ($failures.Count) { throw ($failures -join "`n") }
    Write-Output "[PASS] $($manifest.files.Count) resource files verified (SHA-256). HTP V$arch; QAIRT $($manifest.sdk_version), build $($manifest.sdk_build_id)."
    Write-Output '[INFO] Local resource validation only. This does not establish device compatibility.'
    exit 0
} catch {
    Write-Output "[FAIL] $($_.Exception.Message)"
    exit 1
}
