# Maa-PCR

Windows x64 的 PCR 国服日常与黎明界自动化。提供独立的“日常&扫荡”和“黎明界bot”配置，使用 MaaFramework 识别页面和执行输入。

## 当前状态

当前源码版本为 **v0.1.9 / R53**，基于正式 v0.1.8 / R52，整合多轮 R53 测试版的活动短列表、日常与黎明界局部提速、预览缓存、断线容错、主页入口识别和更新导入改进。构建产物先完成验证，再发布同一精确载荷；实际公开状态以 [GitHub Releases](https://github.com/shiwandehongbai/Maa-PCR/releases) 为准。[发布范围](docs/RELEASE_STATUS.md)和[性能测量](docs/R53_PERFORMANCE.md)列出已有证据及未覆盖项。整体日常和黎明界整链提速尚未实测，不公布整链百分比。版本含义、源码入口及检查命令见[开发指南](docs/DEVELOPMENT.md)。验收须与 `delivery/acceptance.json` 的修订号、源码和载荷同时匹配；旧结果不能代替最终包验证。

日常只运行玩家选定的已支持项目，不包括自动购买、付费扭蛋、重置或推图；`daily_complete=false` 不会因版本发布改成“覆盖全游戏”。黎明界保留完整功能并标为测试版，继承 R38 难度5真实通关证据；同批双成功及随机分支缺口见 [验收范围](docs/RELEASE_STATUS.md)。

黎明界的“保留旧局”默认关闭。再次开始时，程序先核对旧局；旧局仍在进行就撤退，确认结束后新开。需要保留时打开开关，选择会自动保存。无法安全续跑时会提示手动处理；未知撤退不会重复发送。实机断网和强退后的恢复尚未验收。

## 安装与更新

正式包从 [GitHub Releases](https://github.com/shiwandehongbai/Maa-PCR/releases) 获取。解压完整运行包到可写目录后启动 `MFAAvalonia.exe`，无需安装系统 Python；首次配置模拟器与任务后由玩家启动任务。

自 R39 起默认异步检查稳定版，玩家确认后下载并安装，任务运行期间禁止安装。配置、任务顺序、账号数据及未知结果账本保留，升级后只重启软件，不自动继续游戏。R38 首次升级使用精确 R38→R39 引导包中的 `first-update.bat`，不能用旧入口替换正在运行的 Python。[更新与故障恢复](docs/UPDATES.md)。

R53 提供精确正式 R52→R53 增量补丁，其他兼容的较低修订号安装使用完整运行包，经维护器保数据升级。已安装的 R53 测试候选与本版同修订号，普通更新会拒绝；此前的候选替换工具也不能用于正式 R53。请保留测试目录和数据，等待匹配该候选清单的专用迁移。R54 测试版须先使用同时绑定旧、新清单的[专用转换包](docs/R54_TO_R52.md)转为正式 R52。不要解压更新包覆盖原安装。

## 开发

开发环境、公开回归和 GUI 检查统一见 [开发指南](docs/DEVELOPMENT.md)。发行构建入口为 `scripts/build_release.py`，使用 `--help` 查看固定依赖、工具和构建参数；构建产物和日志只写 `.runtime`。

发布构建使用 `Release` 配置和 NuGet `--locked-mode`，提交的 `packages.lock.json` 仅对应 Release。Debug 含额外诊断依赖，开发时须使用独立的 Debug 锁文件；不要用 Debug restore 覆写发布锁。

源码、运行包、补丁和更新索引必须来自同一发布版本。历史原生二进制不宣称逐字节可复现。[贡献与反馈](CONTRIBUTING.md) · [黎明界集成](docs/LABYRINTH_INTEGRATION.md) · [来源和许可证](docs/SOURCE_AND_LICENSES.md)。

黎明界玩家 ID、用户名和密码由 Windows 当前用户 DPAPI 加密保存在本机，默认遮罩；SDK 票据只用于当前会话，验证码人工完成。请勿提交配置、账本、原始日志、截图或凭据；项目使用多种第三方许可证，根 MIT 不覆盖全部组件。
