# 本地开发环境初始化

首次在 Windows 上准备本仓库的开发与验证环境时按本文操作；日常验证范围与契约说明见[开发与验证](DEVELOPMENT.md)。

约定：`<仓库>` 指含 `pyproject.toml`、`src/`、`gui/`、`.github/` 的 checkout 根目录。
版本以 CI 契约为准：**CPython 3.13.16**、**.NET SDK 10.0.101**（Windows x64、PowerShell 7）。

## 0. 前置

git；下文命令均在 PowerShell 7 中执行，其余环境逐节安装。

## 1. 安装 CPython 3.13.16

二选一。两种方式装出来的解释器都必须能作为**非 venv** 解释器直接运行（原因见本节末）。

### 方案 A：mise

```powershell
cd <仓库>
mise use python@3.13.16     # 生成本地 mise.toml（已在 .gitignore 中，不入库）并安装
mise ls python              # 应显示 3.13.16
```

版本不随仓库分发，每人本地自己钉住即可；更新版本时重跑 `mise use`。

### 方案 B：官方安装包单独下载安装

```powershell
cd <仓库>
$exe = '.runtime\installers\python-3.13.16-amd64.exe'
New-Item -ItemType Directory -Force (Split-Path $exe) | Out-Null
curl.exe -L -o $exe 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe'   # 约 30 MB

# 当前用户安装并加入 PATH（不需要管理员；去掉 PrependPath=1 则不改 PATH）
Start-Process -FilePath $exe -Wait -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_test=0'
python -V                    # 新开终端后应为 3.13.16
```

官方目录 <https://www.python.org/ftp/python/3.13.16/> 同时提供 `.sigstore` / `.asc` 签名供校验。

### 装完：确认版本、准备两套解释器与依赖

```powershell
cd <仓库>
python -V                                       # 应为 3.13.16

# A) 验证用解释器
python -m pip install --require-hashes -r requirements-dev.txt

# B) GUI 运行用的 venv
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
```

两套解释器分工不同，缺一不可：

- **A（非 venv）** 供 `scripts/verify.py` 使用。它的契约测试要求 `sys.executable` 就是 socket 的属主进程，而 venv 的 `Scripts\python.exe` 是启动器，会再派生一个子进程，导致测试失败。
- **B（`.venv`）** 是 GUI 运行时的 agent 解释器（`PcrRuntimePaths.ResolvePython` 的顺序为 `runtime/python/python.exe` → `.venv\Scripts\python.exe`）。

## 2. 安装 .NET SDK 10.0.101

`global.json` 用 `rollForward: disable` 精确锁定 SDK，同一 feature band 的其它补丁号都不满足。
下载地址：<https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.101/dotnet-sdk-10.0.101-win-x64.exe>

```powershell
cd <仓库>
$exe = '.runtime\installers\dotnet-sdk-10.0.101-win-x64.exe'
New-Item -ItemType Directory -Force (Split-Path $exe) | Out-Null
curl.exe -L -o $exe 'https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.101/dotnet-sdk-10.0.101-win-x64.exe'
(Get-FileHash $exe -Algorithm SHA512).Hash   # 见官方 release-metadata 公布的校验值

Start-Process -FilePath $exe -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait
dotnet --version                             # 必须输出 10.0.101
```

## 3. 填充 NuGet 缓存

`scripts/verify.py` 的 gui 检查只做**离线**锁定恢复（`NuGet.Offline.Config` 里只有本地缓存一个源），因此这一步是验证的前置条件。

```powershell
cd <仓库>
$common = '--locked-mode','--disable-parallel','-p:Platform=x64','-p:Configuration=Release','-p:UsedAvaloniaProducts=','-p:NuGetAudit=false'

dotnet restore gui\MFAAvalonia\MFAAvalonia.Desktop\MFAAvalonia.Desktop.csproj -r win-x64 @common
dotnet restore gui\MFAAvalonia\PcrUpdate.Tests\PcrUpdate.Tests.csproj -r win-x64 @common
dotnet restore gui\MFAAvalonia\PcrProfileSwitch.Tests\PcrProfileSwitch.Tests.csproj @common
dotnet restore gui\MFAAvalonia\PcrLabyrinthCredentials.Tests\PcrLabyrinthCredentials.Tests.csproj @common
```

- 前两个项目必须带 `-r win-x64`，否则 `--locked-mode` 报 `NU1004`（锁定文件的 RID 与项目不一致）。
- 失败可直接重跑同一条命令，已下载的包会跳过。
- 完成判据：4 条都 `Restored ...`，缓存约 130 个包、2.8 GB，位于 `%USERPROFILE%\.nuget\packages`。
- 如有必要可配置镜像源加速下载。

## 4. 验证

```powershell
cd <仓库>
python -X utf8 -B scripts\verify.py gui      # GUI 契约，几分钟
python -X utf8 -B scripts\verify.py public   # 公开 Python 回归，20 分钟量级
python -X utf8 -B scripts\verify.py all      # 两者依次执行
```

- `python` 必须解析到**非 venv** 的 3.13.16：`python -c "import sys; print(sys.prefix == sys.base_prefix)"` 应为 `True`。
- 结果在 `.runtime/verification/<UTC时间>/result.json`，`status=passed` 才表示该范围全部通过。
- 它不等于发行验收：不打包、不连接设备，不含 CI 的 Bundled Runtime Checks 与密钥扫描。

## 5. 启动 GUI

### 首次

```powershell
cd <仓库>
$app = 'gui\MFAAvalonia\bin\x64\Release'
Set-Content "$app\pcr-repository.txt" (Get-Location).Path -NoNewline    # 绑定 checkout，标题显示「开发模式」
Copy-Item interface.json "$app\interface.json" -Force                 # 任务列表来源
New-Item -ItemType Junction -Path "$app\assets" -Target "$PWD\assets"  # 资源实时同步；只在跑任务时必需
```

### 日常开发

```powershell
cd <仓库>
dotnet build gui\MFAAvalonia\MFAAvalonia.Desktop\MFAAvalonia.Desktop.csproj -c Release -r win-x64 -p:Platform=x64 -p:UsedAvaloniaProducts= --no-restore
Start-Process 'gui\MFAAvalonia\bin\x64\Release\MFAAvalonia.exe'
```

成功标志：标题 `MFA 任务管理器 Maa-PCR（开发模式） | PCR Alpha <版本>`。编译前必须先关 GUI，否则 DLL 被占用。

### 可选：生成一条命令启动的本地脚本

把上面两段合成一个本地脚本（含首次装配、关闭旧实例、增量编译）。`.runtime/` 已 gitignore，脚本只存在于本机：

```powershell
cd <仓库>
New-Item -ItemType Directory -Force .runtime | Out-Null
@'
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)   # 本脚本需放在 <仓库>\.runtime\
$app  = Join-Path $root 'gui\MFAAvalonia\bin\x64\Release'

Set-Content (Join-Path $app 'pcr-repository.txt') $root -NoNewline
Copy-Item (Join-Path $root 'interface.json') (Join-Path $app 'interface.json') -Force
if (-not (Test-Path (Join-Path $app 'assets'))) {
    New-Item -ItemType Junction -Path (Join-Path $app 'assets') -Target (Join-Path $root 'assets') | Out-Null
}

Get-Process MFAAvalonia -ErrorAction SilentlyContinue | Stop-Process -Force

if ($args -notcontains '-NoBuild') {
    dotnet build (Join-Path $root 'gui\MFAAvalonia\MFAAvalonia.Desktop\MFAAvalonia.Desktop.csproj') `
        -c Release -r win-x64 -p:Platform=x64 -p:UsedAvaloniaProducts= --no-restore -v:minimal
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Start-Process (Join-Path $app 'MFAAvalonia.exe')
'@ | Set-Content -Encoding utf8 .runtime\run-gui.ps1
```

之后：

```powershell
.\.runtime\run-gui.ps1            # 编译 + 启动
.\.runtime\run-gui.ps1 -NoBuild   # 只启动
```

## 6. 改了哪里要重启

| 改了 | 要做什么 |
|---|---|
| `gui/MFAAvalonia/**` | 关 GUI → `dotnet build` → 启动 |
| `interface.json` | 重新拷到 exe 目录 → 重启 GUI |
| `assets/**` | 重启 GUI（junction 已同步，无需拷贝） |
| `src/pcr_maa/**`、`scripts/**` | 重启任务即可，不用重启 GUI |

运行数据与日志在 exe 目录：`config\`、`pcr-alpha-v1\`、`logs\log-YYYYMMDD.log`、`temp\`。

## 7. 常见问题

1. **`Owned Agent TCP connection is absent`**：`verify.py` 用了 venv 解释器。venv 的 `python.exe` 是启动器，会再派生一个子进程，契约测试因此匹配不到 socket。改用非 venv 的解释器。
2. **`A compatible .NET SDK was not found`**：SDK 不是 10.0.101。
3. **GUI 能开但任务列表为空**：exe 目录缺 `interface.json`，程序会静默生成空模板。
4. **开始任务报「Python 运行环境缺失」**：`.venv` 被删除。

日志里的 `PluginMgr ... native/plugins ... could not be found` 是 MaaFramework 的正常噪音，不影响运行。
