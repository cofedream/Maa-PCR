# PCR 国服 Maa 日常工具（开发中）


本轮候选目标：PCR 国服 / Windows x64 / MuMu12＋雷电9 / 现有七模块。继续现有 Maa Pipeline、Owner 和 Python Agent；新增模块接现有选集与回执，未来智能规划入口也必须经过本次 Owner 授权，不开放任意输入或清账接口。候选提供本地离线更新与最小诊断导出；最新资格和真实未决以 [CURRENT_STATE.md](CURRENT_STATE.md) 为准，玩家流程见 [使用说明](docs/PLAYER_GUIDE.md)。当前不发布。

目标是其他玩家也能独立使用的 Maa PCR：沿用 MaaFramework、MFAAvalonia 和现有 Python 业务，玩家搜索并选择设备、设置并自动保存、选择任务、点击开始。玩家流程不要求手选 JSON、填写游戏日或重复声明。本机单 MuMu 是当前验收基线，安装路径、连接地址与账户数据跟随玩家选择；当前仍在开发验收。公开源码候选仍 **BLOCKED**：不附带游戏图像模板或 OCR 模型，尚不能作为可运行发行包。

以下2026-09-26记录为历史验收：本机约定日常业务链已在用户全程观看、主控辅助下完成实机首通；相关固化、单笔历史结案与续接已验收。同版本SDK的无设备Agent/Context探针亦通过独立验收。本阶段收工，不代表当前正式入口无人补点、GUI、wheel或干净机器安装已验收，不能据此将产品 `daily_complete` 改为true。后续为原业务共享Context的最小适配和现成GUI接入。项目AGENTS.md所指的唯一CURRENT_STATE为接班入口；历史原件和失败记录保留。

完整运行包的玩家步骤见[使用说明](docs/PLAYER_GUIDE.md)；当前包仍用于本地验收，小范围测试资格尚未完成。

## 开发与维护入口

当前使用方式是保留完整目录结构的**源码目录 + Python 依赖 + 经核准的资源**。需要 Python 3.10+，依赖见 [requirements.txt](requirements.txt)（MaaFramework 5.13.0、NumPy、OpenCV headless、Pillow；另含测试工具）。Windows BAT 使用源码目录中的 `.venv/Scripts/python.exe`，不会自动安装依赖或启动模拟器。准备环境后，在源码根目录运行：

```powershell
.venv/Scripts/python.exe -B scripts/pcr.py --help
.\supported-daily.bat --help
.\supported-daily.bat
```

CLI 无参数和帮助只显示说明；BAT 无参数只显示菜单，选择 0 退出。源码的 `src/`、`scripts/`、`assets/`、`docs/` 和四个 BAT 应保持相对位置。不要将 `pip install` 生成的安装目录或 wheel 当作已验证的资源布局。

默认 CLI 公开源码候选仅含文本，缺少管线引用的游戏模板和 `assets/resource/model/ocr/` 下的 `det.onnx`、`rec.onnx`、`keys.txt`。不能伪造占位文件或复制私人图像来补齐公开包；缺件时不要尝试设备执行或 `doctor`。来源原文见 [OCR README](assets/resource/model/ocr/README.md) 和 [第三方声明](THIRD_PARTY_NOTICES.md)，保留来源不等于已核准权重分发。构建与验收限制见 [候选说明](docs/RELEASE_CANDIDATE.md) 和 [公开测试边界](docs/PUBLIC_TESTING.md)。

维护者另有独立的 GUI 本地源码配套模式，选取桌面项目、当前 Python Agent 和 Maa pipeline，附带明确的 GUI 构建资源；从解压目录恢复依赖并编译，不沿用旧生成缓存。它供源码核验，玩家仍使用完整运行包。入口、清单与资格范围见 [源码候选说明](docs/RELEASE_CANDIDATE.md#local-desktop-source-companion)。

## 本机 GUI 设置

任务草稿自动保存，无设备也可编辑；开始前只校验当前所选任务并可靠保存。槽位选择 1–7，普通为每关本次次数，深域/高难/困难为每关每日目标，0 次明确跳过。日程沿用游戏内计划，选择“日程”并点击开始即可；Maa 不再重复提供九项子设置，使用既有专属确认并在结束后关闭返回。可选导入兼容 v3/v4，不继承旧许可。详情及尚需桌面主控实机核验项见 [GUI 说明](gui/PCR_ALPHA.md)。以下 CLI 契约保持不变。

2026-10-01 当前私有候选包含 Python/Maa/.NET 与 x64 VC++ 运行库。GUI 与包内 Python 的关键 CRT 实际加载路径均已核对为包内文件，正式包内 Agent 正例/Stop 回归通过；当前启动恢复、设置与预检的既有结果按未变源码范围保留。当前 Core 真实逐模块业务、恢复、完整 Windows 视觉、其他玩家机器及分发资格仍待验。证据与维护构建方式见[本机候选说明](docs/BETA_LOCAL.md)。

## 维护 CLI 的首次配置与日常使用

1. 手动登录游戏、整理已勾选的预设，并确认三个业务各自的槽位。名称仅展示，不能根据名称猜槽位。
2. 运行 `supported-daily.bat configure`，填写可信的现有 adb.exe 路径、唯一 IPv4:端口、逻辑及物理 display、槽位与目标。输入 `SAVE_CONFIG` 后首次保存 `.runtime/daily-config.json`；已有文件拒绝覆盖。也可填写 [v2 模板](docs/daily-config.template.json)，模板本身不可直接运行。
3. 运行 `supported-daily.bat check` 做本地检查。v2 配置下 `inspect-daily.bat --no-pause` 也只检查配置，零设备操作。配置不保存游戏日、执行许可或日程逐项许可。
4. 资源、当前物理绑定和当次许可均核实后，才使用菜单中的启动项或 `supported-daily.bat start`。菜单会逐次收集当前游戏日、未重置计数语境、执行许可及最终确认。取消或缺失确认不会启动。

v2 的现场检查必须显式使用 `inspect-daily.bat --observe`，会产生导航输入但不消费。**旧 v1 的 inspect-daily 兼容入口会连接并观察**，不能把 v2 默认零设备的承诺套用于它。具体配置与旧版边界见 [检查说明](docs/INSPECT_DAILY.md) 和 [设备说明](docs/DEVICE.md)；这些文档保留部分历史实现说明，不能作为当前绑定或执行资格。

## 执行范围与结果

`execute-supported.bat` 对接同名 CLI；未传 `--execute` 时拒绝执行。默认顺序为活动困难 → 深域 → 每日任务/礼物 → 高难 → 困难。支持链的深域每关目标为 10，高难与困难为 3；预设由玩家明确配置，不修改已保存的名称、关卡组成或勾选。活动只处理当前已勾选且每日上限为 3 的困难关卡；无法识别活动表示 unknown，不等于未开放。深渊未支持，保持 unknown/unsupported。

日程一键默认关闭；仅本次显式选择、逐项许可和当前计划申报齐备时可作为首步。CLI 对应 `--include-schedule-onekey`、重复 `--schedule-authorize ITEM` 和 `--schedule-attest-current-plan`；项目名见 `scripts/pcr.py execute-supported --help`。玩家自管当前计划，申报只对本次有效，不强制每日做 14 组设置审计；申报也不能替代实际报价、结算读回、设备绑定与未决意图检查。不同连接的日程兼容仍须专项验证。

不购买、不使用宝石、不付费扭蛋、不重置、不推图；已完成项目跳过。目标次数、剩余次数、本次用券数及体力报价仍须核实；库存未知不填 0，明确券不足则停止或安全跳过。只有已有有限适配路径可在本次临时调整使用券张数，安全结束后须恢复读回，不得改保存的预设。

当前物理显示与输入必须指向同一游戏实例；保存的配置、相同分辨率及旧截图均不能证明绑定有效。重启、刷新或重新登录后重新核对。未知结算不重发，不通过换日、清账或新账本绕过未决意图；主页未验证或次数恢复失败停止。`supported_chain_complete` 只表示本次支持范围安全结束，原生成功、导航完成和业务完成分别报告，均不等于完整日常完成。

启动后使用终端打印的 run-id 查询或请求停止：

```powershell
.\supported-control.bat summary --run-id <本次run-id>
.\supported-control.bat stop --run-id <本次run-id>
```

两者只操作本地运行报告，不连接设备；菜单同名命令也可使用。停止请求只在步骤边界生效，在途原生动作不保证立即取消，不自动重发。报告 running 不证明进程仍存活。个人配置、截图、日志及账本保留在本地 `.runtime/`，不要上传。
