param(
    [string]$PlayerExe,
    [ValidateSet('v0.2.7', 'v0.2.8')][string]$TargetVersion = 'v0.2.7'
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $sha.Dispose(); $stream.Dispose() }
}

try {
    if ($TargetVersion -eq 'v0.2.8') {
        $sourceVersion = 'v0.2.7'
        $sourceHash = '2161fc63ed7953e01966663e9b413c8d11d1a2c12c679a7a3c084f5280dc47ab'
        $targetHash = '06e4341ac1134021cb8d834c131fd888110f2405bb378aec13ae3ddacb495a8c'
        $patchHash = 'd4995bf0a1fc50892a982337a13c3047f225012f87731c5bf4176fd4f6820949'
    } else {
        $sourceVersion = 'v0.2.6'
        $sourceHash = '51537f6a646e9611625cb180436ce804dae026ba055d49215f1dfbe7031c2c71'
        $targetHash = '2161fc63ed7953e01966663e9b413c8d11d1a2c12c679a7a3c084f5280dc47ab'
        $patchHash = '330a5f4851d66a2a8d02ac945747cbdaeb7d64cfd03241841f7f1a85dc464188'
    }
    if (-not $PlayerExe) {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = "更新到 ${TargetVersion}：请选择原来使用的 Maa-PCR 主程序"
        $dialog.Filter = 'Maa-PCR 主程序 (MFAAvalonia.exe)|MFAAvalonia.exe'
        try {
            if ($dialog.ShowDialog() -ne 'OK') { exit 0 }
            $PlayerExe = $dialog.FileName
        } finally {
            $dialog.Dispose()
        }
    }
    $PlayerExe = [IO.Path]::GetFullPath($PlayerExe)
    if ([IO.Path]::GetFileName($PlayerExe) -ne 'MFAAvalonia.exe' -or
        -not [IO.File]::Exists($PlayerExe)) { throw '请选择原安装目录中的 MFAAvalonia.exe。' }
    $player = [IO.Path]::GetDirectoryName($PlayerExe)
    $bundle = [IO.Path]::GetFullPath($PSScriptRoot)
    if ($bundle.StartsWith('\\') -or $player.StartsWith('\\')) {
        throw '请使用本地磁盘上的安装目录和解压目录。'
    }
    if ($bundle -eq $player -or $bundle.StartsWith($player + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '请将更新工具解压到原安装目录以外的新文件夹，再双击运行。'
    }
    $manifest = Join-Path $player 'package-manifest.json'
    $marker = Join-Path $player '.runtime\player-maintenance\update-in-progress.json'
    if (Test-Path -LiteralPath $marker) { throw '上次更新尚未完成。请打开原软件，先恢复中断的更新。' }
    $installedHash = Get-Sha256 $manifest
    if ($installedHash -eq $targetHash) { Write-Host "已经是 ${TargetVersion}。"; exit 0 }
    if ($installedHash -ne $sourceHash) {
        throw "这个工具仅适用于官方 ${sourceVersion}。请核对所选安装目录。"
    }
    $patch = Join-Path $bundle "Maa-PCR-${sourceVersion}-to-${TargetVersion}.patch.zip"
    if (-not [IO.File]::Exists($patch) -or (Get-Sha256 $patch) -ne $patchHash) {
        throw '缺少完整的官方增量包。请重新完整解压工具 ZIP，保留里面的补丁 ZIP。'
    }

    # The original updater keeps its recovery workspace outside the installation.
    # Use the extracted bundle's full path for this process and its children only.
    $env:TMPDIR = $bundle
    $env:DOTNET_ROOT = $env:DOTNET_ROOT_X64 = Join-Path $player 'runtime\dotnet'
    $python = Join-Path $player 'runtime\python\python.exe'
    $updater = Join-Path $player 'scripts\player-update.py'
    Write-Host '正在校验并安装。请保持 Maa-PCR 已退出，等更新器自动重新打开软件。'
    $response = & $python -X utf8 -B $updater launch --player $player --archive $patch --archive-sha256 $patchHash
    $code = $LASTEXITCODE
    $result = $response | ConvertFrom-Json
    if ($code -ne 0 -or $result.status -ne 'installer_started') {
        throw ('原更新器未能启动。' + $result.error)
    }
    # A quick worker may have exited before we obtain its process handle.
    $worker = Get-Process -Id $result.worker_pid -ErrorAction SilentlyContinue
    if ($worker) { $worker.WaitForExit() }
    # The restarted GUI can also append its own messages to this worker log.
    $report = Get-Content -LiteralPath $result.log_path -Encoding UTF8 |
        Where-Object { $_.StartsWith('{"status": "updated",') } | Select-Object -First 1
    $completed = if ($report) { $report | ConvertFrom-Json }
    if ($completed.status -ne 'updated' -or $completed.version -ne $TargetVersion -or $completed.gui_started -ne $true -or
        (Test-Path -LiteralPath $marker) -or (Get-Sha256 $manifest) -ne $targetHash) {
        throw ('请核对软件版本，并保留安装目录和解压目录。更新记录在 ' + $result.log_path)
    }
    Write-Host "已更新到 ${TargetVersion}。"
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
