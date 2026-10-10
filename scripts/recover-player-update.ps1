param([Parameter(Mandatory = $true)][string]$Player)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-PlainPath([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if ([bool]$item.PSIsContainer -ne $Directory -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'unsafe_recovery_path' }
    $parent = if ($Directory) { $item.Parent } else { $item.Directory }
    while ($parent) {
        if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'unsafe_recovery_path' }
        $parent = $parent.Parent
    }
}

try {
    $playerRoot = [IO.Path]::GetFullPath($Player).TrimEnd('\')
    if ($playerRoot.StartsWith('\\')) { throw 'unsafe_recovery_player' }
    Assert-PlainPath $playerRoot $true
    $maintenance = Join-Path $playerRoot '.runtime/player-maintenance'
    $receiptPath = Join-Path $maintenance 'external-updater.json'
    $markerPath = Join-Path $maintenance 'update-in-progress.json'
    Assert-PlainPath $receiptPath $false
    Assert-PlainPath $markerPath $false
    if ((Get-Item -LiteralPath $receiptPath).Length -gt 16777216 -or
        (Get-Item -LiteralPath $markerPath).Length -gt 16777216) { throw 'recovery_metadata_too_large' }
    $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($receipt.schema_version -ne 1 -or $receipt.player -ne $playerRoot -or
        $receipt.python -ne 'runtime/python/python.exe' -or $receipt.script -ne 'scripts/player-update.py' -or
        $receipt.base_manifest_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $receipt.base_manifest_sha256 -ne $marker.old_manifest_sha256 -or
        $marker.workspace -notmatch '^update-[0-9a-f]{32}$') { throw 'invalid_recovery_receipt' }
    $work = [IO.Path]::GetFullPath($receipt.workspace).TrimEnd('\')
    Assert-PlainPath $work $true
    if ($work.StartsWith('\\') -or [IO.Path]::GetFileName($work) -notmatch '^update-[0-9a-f]{32}$' -or
        [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($work)) -ne 'Maa-PCR-update' -or
        $work.StartsWith($playerRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'invalid_recovery_workspace'
    }
    $beforeManifest = Join-Path $maintenance ($marker.workspace + '/before/package-manifest.json')
    Assert-PlainPath $beforeManifest $false
    if ((Get-Sha256 $beforeManifest) -ne
        $receipt.base_manifest_sha256) { throw 'recovery_base_hash_mismatch' }
    $before = Get-Content -LiteralPath $beforeManifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($before.update.revision -ne $receipt.base_revision) { throw 'recovery_base_revision_mismatch' }
    $allowed = @('scripts/player-update.py', 'scripts/player-maintenance.py',
        'scripts/recover-player-update.ps1', 'src/pcr_maa/__init__.py',
        'src/pcr_maa/player_update.py', 'src/pcr_maa/player_maintenance.py')
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    if (@($receipt.files).Count -lt 7 -or @($receipt.files).Count -gt 50000) { throw 'invalid_recovery_inventory' }
    foreach ($row in $receipt.files) {
        if ($row.path -notmatch '^(runtime/python/|scripts/|src/pcr_maa/)' -or
            (-not $row.path.StartsWith('runtime/python/') -and $row.path -notin $allowed) -or
            $row.path -match '(^|/)\.{1,2}(/|$)|[\\:*?<>|"]|(^/)' -or
            $row.sha256 -notmatch '^[0-9a-f]{64}$' -or $row.size -lt 0 -or
            -not $seen.Add($row.path)) { throw 'invalid_recovery_inventory' }
        $path = [IO.Path]::GetFullPath((Join-Path $work $row.path))
        if (-not $path.StartsWith($work + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'unsafe_recovery_file' }
        Assert-PlainPath $path $false
        if ((Get-Item -LiteralPath $path).Length -ne $row.size -or
            (Get-Sha256 $path) -ne $row.sha256) {
            throw 'recovery_file_hash_mismatch'
        }
    }
    foreach ($name in $allowed + 'runtime/python/python.exe') {
        if (-not $seen.Contains($name)) { throw 'recovery_file_missing' }
    }
    $python = Join-Path $work $receipt.python
    $script = Join-Path $work $receipt.script
    & $python -X utf8 -B $script worker --recover --player $playerRoot
    exit $LASTEXITCODE
} catch {
    Write-Error ('Recovery stopped without overwriting unknown files: ' + $_.Exception.Message)
    exit 1
}
