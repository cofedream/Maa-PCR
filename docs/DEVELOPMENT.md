# 开发与验证

在包含 `pyproject.toml`、`src/`、`gui/` 和 `.github/` 的 Git checkout 根目录工作；所有命令的工作目录均为该目录。不要在安装目录开发，也不要把不同历史源码树的文件直接覆盖到一起。

## 版本与发布边界

当前开发基线为 **v0.1.3 / R45**。本轮结构整理属于未发布修改，常用安装和正式发行资产保持不变。

- `pyproject.toml` 是 Python 项目的版本元数据。
- `scripts/build_release.py` 的 `--version`、`--revision` 决定候选包身份；当前默认值对应上述基线，不代表修改后的源码已经正式验收。
- `package-manifest.json` 的 `update.version` / `update.revision` 决定玩家实际安装版本；MFAAvalonia 的 2.16.x 是上游 GUI 组件版本。
- `delivery/acceptance.json` 只证明绑定的精确旧提交和载荷。新改动不得借用旧验收晋级；后续正式发行需要新的修订号、候选及受影响验证。
- 已发布范围见 [发布说明](RELEASE_STATUS.md)，历史记录保留当时的版本，不批量替换。

## 本地检查

使用 CPython 3.13 和 `global.json` 固定的 .NET SDK。Python 开发依赖从 `requirements-dev.txt` 安装并校验哈希；GUI 使用 Release 锁文件。复用匹配的本地环境和缓存即可，无需修改玩家运行时。

```powershell
python -X utf8 -B -m pytest -p scripts.public_test_policy -m "not private_replay"
dotnet build gui/MFAAvalonia/MFAAvalonia.Desktop/MFAAvalonia.Desktop.csproj -c Release -r win-x64 -p:Platform=x64 -p:RestoreLockedMode=true -p:UsedAvaloniaProducts=
dotnet run --project gui/MFAAvalonia/PcrUpdate.Tests/PcrUpdate.Tests.csproj -c Release -r win-x64 -p:Platform=x64 -p:RestoreLockedMode=true -p:UsedAvaloniaProducts= -- "$((Get-Location).Path)/.runtime/gui-checks"
```

涉及启动 owner、配置或凭据时，还要运行 `PcrProfileSwitch.Tests`（包含 owner 契约）和 `PcrLabyrinthCredentials.Tests`。这两个检查引用上一步生成的 Core，使用 Release 锁文件且不传 `--runtime`；完整参数与 CI 顺序见 [Windows 检查工作流](../.github/workflows/ci.yml)。凭据检查使用隔离的合成配置和当前 Windows 用户的 DPAPI。

测试禁止发现或构造真实 ADB 控制器。私有截图缺失与未选回放不算通过。GUI 构建、离线契约检查和真实游戏验收是不同证据；本轮只进行前两者。构建与测试输出保留在 checkout 的 `.runtime` 或 GUI 已忽略的 `bin/obj`，不复制到玩家安装。

## 核心职责

- `schedule_onekey.py`：公开入口、当前会话观察、输入守卫与原子账本生命周期。
- `onekey_flow.py`：九行清点及正常日程后继顺序；只通过调用方的受保护 `send` 执行。
- `schedule_recovery.py`：历史未知状态、受限结果关闭、新周期证明；不把旧未知改成已结算。
- `PcrAlphaOwner.cs`：唯一启动 owner 与权限生命周期。
- `PcrAlphaOwner.Session.cs`：同一 owner 内的会话与子进程协议；保留原锁和释放顺序。
- `PcrStartConsent.cs`：本次启动许可的数据与校验；不创建控制器或派发输入。当前界面继续使用原有配置控件，移除的两个旧私有弹窗没有调用入口。

重构保持公开入口、错误码、等待预算、阈值、账本格式和停止/未知结果不重发语义。先运行受影响的错误、停止、重复启动与恢复反例，再运行公开回归和 GUI 契约检查。
