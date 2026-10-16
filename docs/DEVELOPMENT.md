# 开发与验证

在包含 `pyproject.toml`、`src/`、`gui/` 和 `.github/` 的 Git checkout 根目录工作；所有命令的工作目录均为该目录。不要在安装目录开发，也不要把不同历史源码树的文件直接覆盖到一起。

## 版本与发布边界

当前源码版本为 **v0.1.8 / R52（发布准备中）**，升级基线为 v0.1.7 / R51。新构建保持 release_staging / distribution_ready=false；精确载荷验收通过后才封装发布。已安装版本以安装目录的 manifest 为准。

- `pyproject.toml` 是 Python 项目的版本元数据。
- `scripts/build_release.py` 的 `--version`、`--revision` 决定候选包身份；当前默认值对应上述源码版本，不代表修改后的源码已经正式验收。
- `package-manifest.json` 的 `update.version` / `update.revision` 决定玩家实际安装版本；MFAAvalonia 的 2.16.x 是上游 GUI 组件版本。
- `delivery/acceptance.json` 只证明其中修订号对应的精确提交和载荷。新改动不得借用旧验收晋级；后续正式发行需要新的修订号、候选及受影响验证。
- 已发布范围见 [发布说明](RELEASE_STATUS.md)，历史记录保留当时的版本，不批量替换。

## 本地检查

使用完整的 **CPython 3.13**（CI 为 3.13.16）和 `global.json` 固定的 .NET SDK。Python 开发依赖按 `requirements-dev.txt` 校验哈希安装；GUI 依赖预先准备到本地 NuGet 缓存。不要使用玩家的嵌入式 Python 做完整开发测试，它可能缺少 `venv` 或忽略子进程的 `PYTHONPATH`。

统一入口为 `scripts/verify.py`，默认执行公开 Python 回归：

```powershell
python -X utf8 -B scripts/verify.py public
python -X utf8 -B scripts/verify.py gui
python -X utf8 -B scripts/verify.py all
```

- `public` 使用 `scripts.public_test_policy`，明确排除 `private_replay`，不要求 .NET。
- `gui` 校验 SDK 精确版本，依次离线锁定恢复、Release 构建桌面程序，运行 `PcrUpdate.Tests`、`PcrProfileSwitch.Tests`（包括 owner 契约）和 `PcrLabyrinthCredentials.Tests`。每组检查使用的 Core 哈希必须与桌面构建一致。
- `all` 执行上述两组；前置条件或任一步失败就停止并返回非零退出码。`passed` 只表示所选范围的命令成功，不把跳过、未选中或缺失的私有回放计作通过。

入口固定使用所在 checkout 作为源码根目录，不依赖调用方当前目录。通过 `--dotnet` 指定 SDK 可执行文件，通过 `--nuget-cache` 指定已有包缓存；不指定时分别使用 PATH 和 `NUGET_PACKAGES`（未设置则为用户 `.nuget/packages`）。入口不安装依赖、不联网恢复、不打包、不连接设备、不复制到玩家安装。缺依赖时先按锁文件单独准备环境，再重新运行。

每次运行的命令、退出码、日志、公开测试 XML 和 `result.json` 保存在 `.runtime/verification/<UTC时间>/`；入口创建的合成配置和临时环境也在该目录；现有 Python 夹具仍复用 `.runtime/confirmation-timing-work` 保存原生日志和部分临时文件。GUI 编译产物使用源码工程已忽略的 `bin/obj`。凭据检查需要当前 Windows 用户的 DPAPI，使用合成凭据；若运行权限环境不支持 DPAPI，保留失败日志并在正常用户上下文重跑，不能标成通过。

启动边界测试调用真实 RequestStartAsync 和 Toggle 停止入口，覆盖映射读取期间的停止、设备/配置切换阻止、目标或配置漂移拒绝、取消后重启；断言控制器、Agent 和授权均未创建。它使用合成显示信息与配置，不覆盖已运行任务的后台 StopInternal 清理。

测试禁止发现或构造真实 ADB 控制器。GUI 构建、离线契约检查和真实游戏验收是不同证据。此入口覆盖前两者；发行候选、内置运行时检查和实机验收仍按 [Windows 检查工作流](../.github/workflows/ci.yml) 及发行流程执行。

## 核心职责

- `schedule_onekey.py`：公开入口、当前会话观察、输入守卫与原子账本生命周期。
- `onekey_page.py`：同帧页面识别、行与设置标签、一次性输入守卫；日期校验继续使用调度模块的同一历史上下文。旧模块的类与常量导入保持兼容。
- `onekey_flow.py`：九行清点及正常日程后继顺序；只通过调用方的受保护 `send` 执行。
- `schedule_recovery.py`：历史未知状态、受限结果关闭、新周期证明；不把旧未知改成已结算。
- `PcrAlphaOwner.cs`：唯一启动 owner 与权限生命周期。
- `PcrAlphaOwner.Session.cs`：同一 owner 内的会话与子进程协议；保留原锁和释放顺序。
- `PcrStartConsent.cs`：本次启动许可的数据与校验；不创建控制器或派发输入。当前界面继续使用原有配置控件，移除的两个旧私有弹窗没有调用入口。

重构保持公开入口、错误码、等待预算、阈值、账本格式和停止/未知结果不重发语义。先运行受影响的错误、停止、重复启动与恢复反例，再运行公开回归和 GUI 契约检查。
