# Maa-PCR

Windows x64 的 PCR 国服日常与黎明界自动化。提供独立的“日常&扫荡”和“黎明界bot”配置，使用 MaaFramework 识别页面和执行输入。

## 当前状态

当前源码版本为 **v0.1.6 / R50**，基于 v0.1.5 / R49，合并日常拖动与识别优化、渠道预设、日志导出与保留规则，以及黎明界遗物名称识别修复。构建产物先作为候选完成验证，再晋级同一精确载荷；实际发布状态以 [GitHub Releases](https://github.com/shiwandehongbai/Maa-PCR/releases) 为准。版本含义、源码入口及检查命令见 [开发指南](docs/DEVELOPMENT.md)。速度优化已实现，整体日常提速和新完整游戏链实机对照尚未验证，不宣称提速百分比。实机性能对照、六个游戏日和40%均不作为发布前置条件；发布仍要求离线检查、两配置GUI、软件内更新/恢复、干净构建和精确载荷绑定通过。历史证据范围见 [R39 验收](docs/R39_ACCEPTANCE.md)，当前验收的修订号、精确载荷绑定与复用范围见 `delivery/acceptance.json`；仅修订号和载荷均匹配时才适用，旧绑定不证明新改动已验收。

日常只运行玩家选定的已支持项目，不包括自动购买、付费扭蛋、重置或推图；`daily_complete=false` 不会因版本发布改成“覆盖全游戏”。黎明界保留完整功能并标为测试版，继承 R38 难度5真实通关证据；同批双成功及随机分支缺口见 [验收范围](docs/RELEASE_STATUS.md)。

黎明界的“保留旧局”默认关闭。再次开始时，程序先核对旧局；旧局仍在进行就撤退，确认结束后新开。需要保留时打开开关，选择会自动保存。无法安全续跑时会提示手动处理；未知撤退不会重复发送。实机断网和强退后的恢复尚未验收。

## 安装与更新

正式包从 [GitHub Releases](https://github.com/shiwandehongbai/Maa-PCR/releases) 获取。解压完整运行包到可写目录后启动 `MFAAvalonia.exe`，无需安装系统 Python；首次配置模拟器与任务后由玩家启动任务。

自 R39 起默认异步检查稳定版，玩家确认后下载并安装，任务运行期间禁止安装。配置、任务顺序、账号数据及未知结果账本保留，升级后只重启软件，不自动继续游戏。R38 首次升级使用精确 R38→R39 引导包中的 `first-update.bat`，不能用旧入口替换正在运行的 Python。[更新与故障恢复](docs/UPDATES.md)。

R49 用户可用精确 R49→R50 增量补丁，其他兼容正式版本使用完整运行包，经维护器保数据升级。本地 R50 测试候选须使用同时绑定旧、新清单的专用晋级步骤，普通同修订号更新仍会拒绝。

## 开发

开发环境、公开回归和 GUI 检查统一见 [开发指南](docs/DEVELOPMENT.md)。发行构建入口为 `scripts/build_release.py`，使用 `--help` 查看固定依赖、工具和构建参数；构建产物和日志只写 `.runtime`。

发布构建使用 `Release` 配置和 NuGet `--locked-mode`，提交的 `packages.lock.json` 仅对应 Release。Debug 含额外诊断依赖，开发时须使用独立的 Debug 锁文件；不要用 Debug restore 覆写发布锁。

源码、运行包、补丁和更新索引必须来自同一发布版本。历史原生二进制不宣称逐字节可复现。[贡献与反馈](CONTRIBUTING.md) · [黎明界集成](docs/LABYRINTH_INTEGRATION.md) · [来源和许可证](docs/SOURCE_AND_LICENSES.md)。

黎明界玩家 ID、用户名和密码由 Windows 当前用户 DPAPI 加密保存在本机，默认遮罩；SDK 票据只用于当前会话，验证码人工完成。请勿提交配置、账本、原始日志、截图或凭据；项目使用多种第三方许可证，根 MIT 不覆盖全部组件。
