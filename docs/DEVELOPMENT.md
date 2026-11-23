# 开发与验证

在包含 `pyproject.toml`、`src/`、`gui/` 和 `.github/` 的 Git checkout 根目录工作；所有命令的工作目录均为该目录。不要在安装目录开发，也不要把不同历史源码树的文件直接覆盖到一起。

## 正式发布验收

发布前，用上一正式版的独立安装分别测试增量包和完整包，核对数据保留和回滚结果。将验收过的 CI 候选包记录在 `delivery/acceptance.json`，再推送正式标签。GitHub Actions 接手后本机即可关机，云端检查通过后会自动发布。

### 常规发布后的核对

1. GitHub Releases 的最新稳定版标签与本次发布的标签一致。
2. `SHA256SUMS.txt` 列出的附件齐全，GitHub 显示的附件 SHA-256 与清单一致。
3. 从公开的 `releases/latest/download/update-index.json` 读取更新索引，用上一正式版的安装清单核对选包结果。目标版本应为本次发布，选中的应是从上一正式版升级的增量包。

以上三项通过即完成常规核对。只有更新器、打包逻辑或下载流程有改动，或者玩家反馈升级失败时，才用上一正式版的独立安装从公开入口完整升级一次，验证下载、安装、重启和数据保留。

## 版本与发布边界

从 v0.2.0 起，客户端、发布标题和下载文件名统一使用公开版本号。后续修复依次发布为 v0.2.1、v0.2.2。Git 标签沿用 `v` 前缀。每次发布仍须递增清单中的整数 `revision`，供已安装的客户端检查更新和校验补丁。

v0.2.3 使用内部修订号 91。正式 v0.2.2 使用增量包 `Maa-PCR-v0.2.2-to-v0.2.3.patch.zip`。其他受支持的旧版由更新器选择完整包。

`scripts/release_baselines.py` 记录当前发布版本、上一版修订号和正式清单的 SHA-256。构建和封版使用 `--release-baseline` 读取同一份配置。Windows 工作流先核对源码版本和基线，再安装构建依赖、运行测试。

- `pyproject.toml` 是 Python 项目的版本元数据。
- `scripts/build_release.py` 的 `--version`、`--revision` 决定候选包身份；当前默认值对应上述源码版本，不代表修改后的源码已经正式验收。
- `package-manifest.json` 的 `update.version` / `update.revision` 决定玩家实际安装版本；MFAAvalonia 的 2.16.x 是上游 GUI 组件版本。
- `delivery/acceptance.json` 只证明其中修订号对应的精确提交和载荷。新改动不得借用旧验收晋级；后续正式发行需要新的修订号、候选及受影响验证。
- 已发布范围见 [发布说明](RELEASE_STATUS.md)，历史记录保留当时的版本，不批量替换。

R67 整合半截卡片、开局衔接修复和全部 11 组上游功能。新包绑定本版 CI 和升级验收；已有真实游戏验证边界保持，旧验收和跳过项不能代替本版结果。

## 当前整合范围

2026-10-22 的公会战候选已接入当前主线源码，保留 R62 启动、更新器与极难节点修复，以及 R63 运行反馈、目标摘要和保存状态。界面入口显示“公会战”，内部配置标识仍为 `pcr-agent-v1`。作业和图片首次进入或展开时才加载，切页复用已有控件。

本次发行验证使用离线夹具和隔离安装，不覆盖现用 R63 测试版或操作游戏。真实校准清单仍为空，单次模拟在执行前停止，正式出刀和补偿生产入口继续关闭。当前操作流程与待验范围见[公会战作业](PCR_AGENT_CLAN.md)。

## 日常核销证据的保留

深渊和深域的维护核销会在写入终态前，将步骤报告、关联截图和原生日志一起保存到对应账本的 `result-evidence` 目录。文件按内容摘要命名，写入后刷盘；读取副本时继续核对摘要。普通 `debug` 日志和截图可以过期清理，账本及其证据目录应随私有数据保留。

历史记录和当前操作分别校验。账本顺序、前缀摘要、终态绑定和未知操作保护始终有效；当天已核销记录缺少证据时仍停止。只有早于当前游戏日的已核销记录，在原附件和长期副本均缺失时，才记录 `historical_evidence_unavailable` 并继续读取当前画面。可用附件内容变化、非法路径或恢复清单损坏仍停止，不归入附件缺失。完整历史审计仍要求全部证据，缺失文件不视为已恢复。

继续执行还须通过当前游戏日、活动身份、当前次数、报价和最终确认检查。新一期测试应覆盖旧附件缺失、当前画面读取失败、同日再次消费、旧操作仍为 `in_flight` 和证据篡改；不能靠删除账本或补成功状态恢复。

## 本地检查

使用完整的 **CPython 3.13**（CI 为 3.13.16）和 `global.json` 固定的 .NET SDK。Python 开发依赖按 `requirements-dev.txt` 校验哈希安装；GUI 依赖预先准备到本地 NuGet 缓存。不要使用玩家的嵌入式 Python 做完整开发测试，它可能缺少 `venv` 或忽略子进程的 `PYTHONPATH`。首次准备环境（Python 与 .NET SDK 安装、NuGet 缓存、GUI 本地启动）见[本地开发环境初始化](DEVELOPMENT_SETUP.md)。

统一入口为 `scripts/verify.py`，默认执行公开 Python 回归：

```powershell
python -X utf8 -B scripts/verify.py public
python -X utf8 -B scripts/verify.py gui
python -X utf8 -B scripts/verify.py all
```

- `public` 使用 `scripts.public_test_policy`，明确排除 `private_replay`，不要求 .NET。
- `gui` 校验 SDK 精确版本，依次离线锁定恢复、Release 构建桌面程序，运行 `PcrUpdate.Tests`、`PcrProfileSwitch.Tests`（包括 owner 契约）、`PcrLabyrinthCredentials.Tests`、`PcrLabyrinthAccounts.Tests` 和 `PcrLabyrinthRatings.Tests`。后两组覆盖账号迁移、隔离保存及评分窗口。每组检查使用的 Core 哈希必须与桌面构建一致。
- `all` 执行上述两组；前置条件或任一步失败就停止并返回非零退出码。`passed` 只表示所选范围的命令成功，不把跳过、未选中或缺失的私有回放计作通过。

入口固定使用所在 checkout 作为源码根目录，不依赖调用方当前目录。通过 `--dotnet` 指定 SDK 可执行文件，通过 `--nuget-cache` 指定已有包缓存；不指定时分别使用 PATH 和 `NUGET_PACKAGES`（未设置则为用户 `.nuget/packages`）。入口不安装依赖、不联网恢复、不打包、不连接设备、不复制到玩家安装。缺依赖时先按锁文件单独准备环境，再重新运行。

每次运行的命令、退出码、日志、公开测试 XML 和 `result.json` 保存在 `.runtime/verification/<UTC时间>/`；入口创建的合成配置和临时环境也在该目录；现有 Python 夹具仍复用 `.runtime/confirmation-timing-work` 保存原生日志和部分临时文件。GUI 编译产物使用源码工程已忽略的 `bin/obj`。凭据检查需要当前 Windows 用户的 DPAPI，使用合成凭据；若运行权限环境不支持 DPAPI，保留失败日志并在正常用户上下文重跑，不能标成通过。

启动边界测试调用真实 RequestStartAsync 和 Toggle 停止入口，覆盖映射读取期间的停止、设备/配置切换阻止、目标或配置漂移拒绝、取消后重启；断言控制器、Agent 和授权均未创建。它使用合成显示信息与配置，不覆盖已运行任务的后台 StopInternal 清理。

连接回归调用真实 ConnectPcrAdb 和 StopTask，使用合成 MaaCustomController 阻塞连接、首帧和旧预览清理。测试覆盖停止后的迟到返回、重复停止、超时、清理阻塞或异常、渲染等待、直接启动拒绝和后续重连。日志回归通过实际导出入口验证超过旧分卷阈值仍只生成一个 ZIP，并逐项核对包内字节。轮换回归调用真实界面日志入口，覆盖 64 MiB 阈值、重启后的轮换和两份保留上限；导出并发互斥及原分卷底层的文件保护检查继续保留。

测试禁止发现或构造真实 ADB 控制器。GUI 构建、离线契约检查和真实游戏验收是不同证据。此入口覆盖前两者；发行候选、内置运行时检查和实机验收仍按 [Windows 检查工作流](../.github/workflows/ci.yml) 及发行流程执行。

## 多 worktree 并行开发与 GUI 核对

同一台机器可以同时检出多个 worktree。每个 worktree 的 `gui\MFAAvalonia\bin\x64\Release` 都是独立的程序目录，配置、日志和 `pcr-alpha-v1` 都落在各自目录，不会覆盖 main 或其他 worktree 的账号与设置。开发模式下窗口标题和可见标题栏会追加 ` · <分支名>`；没有可读分支时使用 HEAD 短提交号或 checkout 目录名，因此 main 与各 worktree 的窗口可以直接区分。

### 首次准备 worktree 程序目录

在 **worktree 根目录**执行：

```powershell
$root = (Get-Location).Path
$app = Join-Path $root 'gui\MFAAvalonia\bin\x64\Release'
Set-Content (Join-Path $app 'pcr-repository.txt') $root -NoNewline
Copy-Item (Join-Path $root 'interface.json') (Join-Path $app 'interface.json') -Force
if (-not (Test-Path (Join-Path $app 'assets'))) {
    New-Item -ItemType Junction -Path (Join-Path $app 'assets') -Target (Join-Path $root 'assets') | Out-Null
}
```

`pcr-repository.txt` 决定开发模式读取哪个 checkout；`interface.json` 提供任务列表；`assets` junction 让 worktree 实时使用自己的资源。不要从 main 复制 `config`、`pcr-alpha-v1` 或 `logs`，否则会串用账号、策略和日志。

### 构建与启动

```powershell
dotnet build gui\MFAAvalonia\MFAAvalonia.Desktop\MFAAvalonia.Desktop.csproj -c Release -r win-x64 -p:Platform=x64 -p:UsedAvaloniaProducts= --no-restore
Start-Process (Join-Path $app 'MFAAvalonia.exe')
```

成功时标题形如 `MFA 任务管理器 Maa-PCR（开发模式） | ... · <分支名>`。编译前先关闭当前 checkout 的 GUI，否则 DLL 被占用。

### 人工验证窗口（WebView2）

黎明界登录需要人工完成官方极验时，程序在主窗口内嵌 WebView2 显示一次性验证页；开发模式同样依赖系统安装的 WebView2 Evergreen 运行时（不随程序分发，仓库只用 NuGet 的 `Microsoft.Web.WebView2` 托管/loader 组件）。

- 内嵌窗口的用户数据目录按顺序选择：`%LOCALAPPDATA%\MaaPCR-WebView2` → `%TEMP%\MaaPCR-WebView2` → `<程序目录>\temp\WebView2`。用计划任务、代理终端或重定向 TEMP 的方式启动时，进程拿到的临时目录可能不可写，导致 WebView2 环境创建失败；上面的顺序会跳过这类目录。
- 内嵌窗口仍失败时如实回退系统浏览器，并把同一次验证链接复制到剪贴板；界面提示会说明“链接已复制，浏览器没出现时可粘贴打开”。验证页地址不写入日志、错误文本或诊断导出。
- 日志出现 `软件内人工验证窗口不可用（阶段 …，异常类型）` 时按阶段排查：`window`=主窗口/原生句柄、`environment`=WebView2 运行时或用户数据目录、`controller`=原生子窗口创建、`show`=遮罩层显示。缺少 Evergreen 运行时只能安装运行时或走浏览器回退。
- 核对内嵌窗口时建议从资源管理器双击 Release 目录的 `MFAAvalonia.exe`（正常用户上下文），而不是从计划任务/受限终端启动；两种方式都能跑业务，但前者更接近玩家双击启动的环境。

### 并行运行限制

- 普通模式有单实例互斥锁 `PCRAlphaV1.FixedDeviceOwner`，同一 Windows 用户会话只能有一个完整 GUI；启动第二个会直接退出、不显示窗口。需要切换 worktree 时，先在正在运行的窗口中停止任务并正常关闭，再启动另一个。不要强杀正在执行任务的实例。
- 只想并行核对**设置界面**（不改 main、不跑任务）时，可用零设备的设置隔离诊断模式：
  1. 把 worktree 的 Release 输出复制到 `<worktree>\.runtime\player-win32-20260929\count-restore-fix\formal-settings\app`，再把 `assets` 作为真实目录复制进去（不能是 junction）；该目录树及其所有父目录都不能含 reparse point。
  2. 确保 app 目录有指向 worktree 根目录的 `pcr-repository.txt` 和 `interface.json`。
  3. 在 app 目录写入 `player-settings.ready`，内容严格为 `PCR PLAYER SETTINGS COUNT RESTORE WIN32 ONLY` 加一个 LF，不能带 CRLF 或 BOM。
  4. 启动：`dotnet <app>\MFAAvalonia.dll --pcr-settings-diagnostic`。该模式使用独立互斥锁 `PCRAlphaV1.PlayerSettingsDiagnostic`，可以和 main 同时运行；标题会追加 `— PCR 玩家设置隔离验收（零设备）`，只能编辑设置，不能开始任务，同一时间只允许一个诊断窗口。
  5. 检查完成后关闭窗口；隔离副本只用于界面核对，可随时删除。

  ```powershell
  $app = Join-Path (Get-Location) '.runtime\player-win32-20260929\count-restore-fix\formal-settings\app'
  robocopy 'gui\MFAAvalonia\bin\x64\Release' $app /E /XJ /NFL /NDL /NJH /NJS /NP
  robocopy 'assets' (Join-Path $app 'assets') /E /XJ /NFL /NDL /NJH /NJS /NP
  [IO.File]::WriteAllText((Join-Path $app 'player-settings.ready'),
      "PCR PLAYER SETTINGS COUNT RESTORE WIN32 ONLY`n", [Text.UTF8Encoding]::new($false))
  ```
- 不要同时在两个窗口对同一模拟器执行任务；ADB、控制器和 Agent 会话不属于并行开发支持范围。
- 仅核对 GUI 不需要 worktree 的 `.venv`；要从 worktree 真正跑任务，先按[本地开发环境初始化](DEVELOPMENT_SETUP.md)为该 worktree 准备 Python 环境，并确认没有其他实例占用同一设备。

## 性能与场景验收

性能分析复用 `src/pcr_maa/labyrinth/timing.py` 的 `Timings` 和 `PcrCapturePerformance` 报告。分别记录启动、连接准备、识别和任务执行耗时，在同一设备、相同设置与相近任务量下比较。至少收集 3 组可比样本再报告整体提速，注明冷启动或预热条件，保留慢样本。局部识别或缓存耗时不能换算成整局提速；不为取证新增常驻监控、轮询抓屏或全目录扫描。

近期识别故障优先保留修复所需且获准使用的脱敏原图和路线，补能在旧代码上失败的回归。真实使用验收关注触发场景、完整后继及新增失败；未变更的已通过检查不无故重跑。反馈内容见[贡献与反馈](../CONTRIBUTING.md)。

公会战的首个真实验收目标为：已核对的纯 AUTO 作业完成一次模拟，读到实际伤害并返回。随后分别验收正式出刀和补偿。页面整理、离线业务测试和三刀预设均不能证明真实执行可用；游戏操作仍须在明确授权的验收阶段进行。

## R58 识别、导出与版本显示边界

开局及奖励共用加入读回，原生任务、头像和姓名证据由上层同步核验。冷 OCR 在 Owner 登录和业务前准备，原 3 秒业务预算保持；Stop、超时及失败均不继续登录。规范失败证据导出只生成脱敏副本。具体实现、保存帧与压力边界见 [OCR 稳定性](OCR_STABILITY.md)。

标题与更新显示统一读取实际安装清单。发布测试应在精确候选包与隔离升级后确认新版本显示，不能仅检查 interface.json 的旧资源字段或本地编译 DLL。日常／黎明界日志按档案提供选项，全部导出继续保持文件白名单和未知记录。

## R57 整合与修复边界（历史）

本版保留 PR #3 的贡献历史，增加黎明界脱敏业务日志和五公会预置队伍界面。选人先判断是否已选：已选旧 ID 和占位项允许移除，未选占位项禁止新增。三个槽位逐项绑定移除命令；旧 `opening_rosters` 格式、未知 ID 和多候选顺序保持，全部清空后使用默认方案。占位项标为“未列入预置名单”。

普通日志目录与黎明界业务目录分别传入占用统计和导出入口，普通占用继续包含诊断截图，业务账本完整导出，链接路径继续拒绝。开发启动脚本在写入、构建及启动前检查当前 checkout 的准确程序路径；当前实例运行中或路径不可读时停止，不提权、不强杀，`-NoBuild` 同样检查。

回归覆盖有效旧 ID `1801`、未知 ID、旧候选顺序、图鉴缺失、移除后保存重载、清空恢复默认，以及虚构进程和分离日志目录。公开回归、桌面构建和离线 GUI 契约必须绑定最终源码及 CI 载荷；原生检查限时运行并确认释放后以 0 退出。本轮不操作真实安装、账号、设备或游戏。

## R56 测试修复边界（历史）

导出窗口与“关于”页共用单包导出入口，一次只写入用户选定的 ZIP。界面日志沿用 Serilog 按天分文件，单份达到 64 MiB 时轮换，最多保留两份（含当前文件）。约 128 MiB 是正常界面日志的容量规模，不是整个程序的硬上限；单条超长记录、占用导致无法删除的旧文件及独立运行记录不受这个估算保证。

取消一键清理及应用额外的按天清理。发行包由 `requirements-runtime.lock` 统一锁定 Python 和 GUI 使用的 MaaFramework 运行库。运行库自带日志轮换及初始化时的过期文件清理。日志规则须以最终包的原生 DLL 在隔离目录中验证。应用保留 `.runtime` 下的历史运行目录、业务账本和检查点。日志占用统计包含 `debug/on_error` 和 `debug/vision` 图片。长期排错材料需提前导出。

已有实例配置读取失败或不是 JSON 对象时，加载直接报错，保留原文件；只有文件确实不存在时才允许创建空配置。全局配置损坏时，保存和删除报告错误并保留原字节；有效配置改用已有原子写入逻辑，避免中途写断。原配置修好或占用解除后可重新加载。

Python 和 GUI 的 OCR 取证锁由操作系统持有，进程退出后自动释放。锁文件保留在原处，因此看到 `.quota-lock` 不代表仍被占用。活跃写入仍互斥，截图配额、业务输入和未知结果不重发规则保持。

回归使用合成配置、凭据字段和运行记录，覆盖文件占用、损坏 JSON、正常恢复、原子替换失败，以及子进程在持锁时直接退出。它们不替代真实设备、真实账号或游戏验收。

## R55 修复边界（历史）

黎明界导航记录同时写入相同的 `enter_id` 和 `protocol_enter_id`，恢复逻辑兼容旧字段。只有明确列出的纯客户端导航允许局号为 0；消费和协议创建后的 Stop/Start 仍要求正局号。冲突、缺失或非法字段继续阻断。程序先认证并核对同账号、同设备的服务器状态，再按原策略处理旧局；历史未知记录保留原字段和未知结果，不补成功，不重发旧操作。

日志分卷导出先检查全部目标，再独占创建新文件。同名文件或目录会使本次导出失败。Windows 导出期间持有新文件句柄，失败时只标记本次确实新建的文件删除，避免按路径误删外部替换文件。

连接与首帧准备的界面等待预算为 30 秒。停止或超时只取消等待和后续工作，原生调用没有可用的中断接口。连接会话继续监督原生调用和资源清理，全部完成后才解除隔离；清理结果无法确认时，保留资源和隔离，提示重启软件。旧会话不能发布迟到画面、复用控制器或影响新会话。真实 ADB 卡死和实机资源回收尚未验证。

## 核心职责

- `schedule_onekey.py`：公开入口、当前会话观察、输入守卫与原子账本生命周期。
- `onekey_page.py`：同帧页面识别、行与设置标签、一次性输入守卫；日期校验继续使用调度模块的同一历史上下文。旧模块的类与常量导入保持兼容。
- `onekey_flow.py`：九行清点及正常日程后继顺序；只通过调用方的受保护 `send` 执行。
- `schedule_recovery.py`：历史未知状态、受限结果关闭、新周期证明；不把旧未知改成已结算。
- `PcrAlphaOwner.cs`：唯一启动 owner 与权限生命周期。
- `PcrAlphaOwner.Session.cs`：同一 owner 内的会话与子进程协议；保留原锁和释放顺序。
- `PcrStartConsent.cs`：本次启动许可的数据与校验；不创建控制器或派发输入。当前界面继续使用原有配置控件，移除的两个旧私有弹窗没有调用入口。
- `PcrPreviewConnection.cs`：独立连接的原生调用、取消和资源所有权；原生调用或清理未完成时持续隔离。

重构保持公开入口、错误码、等待预算、阈值、账本格式和停止/未知结果不重发语义。先运行受影响的错误、停止、重复启动与恢复反例，再运行公开回归和 GUI 契约检查。
