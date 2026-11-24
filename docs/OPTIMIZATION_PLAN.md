# 性能优化总纲（准备实施）

状态：准备实施。本分支 `perf-optimization-plan`（基于 `6a079b8` / v0.2.3）只包含 `docs/OPTIMIZATION_PLAN.md` 与 `docs/LABYRINTH_PERFORMANCE_PLAN.md` 两份文档，业务代码未改。

本文件是 2026-11-24 调研的结论与执行顺序；局内细项、逐项收益、反例清单见[黎明界局内耗时记录与 L1/L2/L3 优化计划](LABYRINTH_PERFORMANCE_PLAN.md)。两份文档的分工：本文回答"先做什么、为什么、怎么验收"，后者回答"局内每一处怎么改"。

边界：先修度量，再按 L1 → L2 → L3 推进；抓帧后端单列为评估线，不与识别优化混在同一批提交。任何整链百分比都必须满足[开发指南](DEVELOPMENT.md)的三组可比样本要求。

---

## 0. 一页结论

1. **不把识别取帧换成 scrcpy。** 它缩短的是"看屏幕"的延迟，不是本项目的流程延迟；且会撞上"截图绑物理屏、输入绑逻辑屏"的安全模型与取证链。
2. **目标设备是 MuMu 时，框架内对应的快抓帧是 `EmulatorExtras`（Maa 标注 Very Fast、无损；BAAS 的同类实现 nemu 实测 5–20 ms），不是 scrcpy。** 但它需要 MuMu SDK 绑定与身份证明，正式入口当前被 `RequireProductionQualification()` 阻断，不是"加个掩码"就能开。
3. **本项目的瓶颈是识别与证据扫描，不是抓帧。** 用户日志一次全场景识别 12.096 s / 123 次（≈98 ms/次）；`NodePixels` 全帧多模板 1.17–1.28 s/帧；节点固定开销 ~51 ms；抓帧是几十到一百多 ms 级。
4. **先修度量，但不要修错对象。** 识别 `[cost=]` 可以修；**ADB 抓帧没有原生耗时行**，`timings.screenshot` 不能靠改正则获得，只能靠外部采样与残差估算（见 §3）。
5. **可迁移的是 BAAS 的"少截 / 少搜 / 少判"**：先验 ROI、一帧多判、廉价前置门、最小证据集；不迁移它的 C++ OCR 服务 + HTTP + 共享内存架构。

---

## 1. 证据基线

| 指标 | 数值 | 来源 | 可信度 |
|---|---|---|---|
| 全场景识别（用户机） | 12.096 s / 123 次 ≈ 98 ms/次 | [RELEASE_STATUS.md:273](RELEASE_STATUS.md#L273) | 用户日志 |
| 节点级固定开销 | ~51 ms/节点；OCR 8–9 ms/词、图标 <1 ms/个 | [LABYRINTH_INTEGRATION.md:162](LABYRINTH_INTEGRATION.md#L162) | 现场实测 |
| `NodePixels.collect` | 1.17–1.28 s/帧（16 模板、4 线程、纯 CPU） | [LABYRINTH_PERFORMANCE_PLAN.md:51](LABYRINTH_PERFORMANCE_PLAN.md#L51) | 本机 Linux 离线 |
| 账本 `events()` | 10.91 s 深拷贝 / 15.06 s 每局首次全量读+校验 | [LABYRINTH_INTEGRATION.md:81](LABYRINTH_INTEGRATION.md#L81) | 本机实测 |
| 日常整轮 | 11 分 06.685 秒 | [R53_PERFORMANCE.md:46](R53_PERFORMANCE.md#L46) | 正式 R52 自然运行 |
| 截图 8→1 的尝试 | 热路径反而回退 ~100 ms，无净提速 | [R53_PERFORMANCE.md:15](R53_PERFORMANCE.md#L15) | 交替 A/B |
| 运行期预览 | 读业务缓存，不新增抓屏 | [R53_PERFORMANCE.md:40](R53_PERFORMANCE.md#L40) | 已实施 |

结论：抓帧不是当前最大的单项；识别/决策的"次数 × 单次成本"才是。抓帧占比的具体数值仍是未知项，由 §3 的 P0 补齐。

---

## 2. 抓帧后端：结论与边界

### 2.1 外部参照（BAAS）

BAAS 不使用 MaaFramework。它的官方速度表：`nemu`（MuMu IPC）**5–20 ms、无损**，`scrcpy` **约 17 ms、有损**，`adb` / `uiautomator2` **约 300 ms**；默认配置反而是 `uiautomator2`，`screenshot_interval` 硬下限 0.3 s，所以 adb → nemu 的实际循环只快约 1.9 倍，不是 15 倍。它把 scrcpy 作为**第二选择**，且只镜像默认显示屏（启动参数没有 `display_id`），控制与取流共用同一条连接。

### 2.2 为什么本项目不换 scrcpy

- **显示绑定**：本项目截图绑物理 viewport（`screencap -d <物理 ID>`）、输入绑逻辑 display（`input -d <逻辑 ID>`），两条命令分别指向两个 ID 并由 dumpsys 互相印证。scrcpy 一个会话只有一个 `--display-id`，取流和注入共用它。
- **像素语义**：scrcpy 默认 H.264、8 Mbps、可变帧率，是有损；本项目大量 0.8/0.85 阈值、窄 RGB 色域判定、同帧图标 id、保存帧回放都建立在无损确定性像素上。
- **新鲜帧**：Maa 的 `screencap` 是调用时刻的新样本；scrcpy 只在画面变化时产帧，"最新帧"可能是点击前的画面。
- **取证链**：GUI 的 `PcrAdbDiscovery.Describe` 强制 `StandardCapture`，`capture_performance.py` 只接受 `(6,1)/(2,0)/(4,0)`，`PcrCapturePerformance` 把 `screencap_methods=6 / input_methods=1` 写死。换源会让截图后端观测变成 `unknown`。
- **实现代价**：只剩 `MaaCustomController` 一条路，要自己实现 screencap + 全部输入 + 启动/停止/shell/UUID，并新增 scrcpy-server 与解码依赖。

### 2.3 EmulatorExtras 也不是配置开关

`EmulatorExtras`（MuMu 12 / LDPlayer 9 / Androws）在 Maa 里走模拟器 SDK 取帧，需要 `extras.mumu` 的 `path / lib / index / app_cloned_index` 绑定。这正好是 `Options()` 构造、`RequireProductionQualification()` 阻断的路径（[PcrMuMuDisplay.cs:225](../gui/MFAAvalonia/MFAAvalonia/Pcr/PcrMuMuDisplay.cs#L225)）；标准 ADB 路径没有 extras 配置。就算把掩码加上，`Describe` 会改回 `StandardCapture`，证据解析也只认 `(6,1)`。因此它是**三处代码 + 重新建立 package/display/input 身份证明**的独立项目，不是 L0 配置项。

已有的实验入口：[PcrMuMuReadOnlyDiagnostic.cs:73](../gui/MFAAvalonia/MFAAvalonia/Pcr/PcrMuMuReadOnlyDiagnostic.cs#L73) 可以单次、只读、无输入地取一帧 EmulatorExtras 画面。抓帧评估线可以在此基础上做"同一画面 adb vs extras 像素一致性 + 50 次耗时 P50"的 A/B，通过后再谈正式资格。**该评估线在 P0 给出抓帧占比之后才启动；占比 <10% 直接关闭。**

---

## 3. P0：度量修复（先做，独立提交）

### P0-1 识别耗时（可修）

- 现状：`timing.py:202` 期望 `VisionNS::(OCRer|...)::analyze].*[cost=Nms]`。
- 真实日志：MaaUtils 在 MSVC 下用 `__FUNCTION__`（类限定、无参数，形如 `[OCRer::analyze]`；以真实 Windows 日志为准），GCC/Clang 下用 `__PRETTY_FUNCTION__`（`[void MaaNS::VisionNS::OCRer::analyze()]`）；`[cost=Nms]` 由 `VAR(cost)` 产生，DEBUG 级。
- 修法：按**文件**匹配，不再依赖限定名：
  `\[(OCRer|TemplateMatcher|ColorMatcher|FeatureMatcher|NeuralNetworkClassifier|NeuralNetworkDetector)\.cpp\][^\n]*\[cost=(\d+)ms\]`
- 本机（Linux）真实日志验证：当前识别正则 **0 命中**，按文件匹配 **93 条**；Windows 行形可能略有差异，但按文件匹配对两种构建都成立。
- 测试：`tests/test_performance_timings_r53.py` 同时加入 MSVC 与 GCC 两种行形；真实一局断言 `recognition.count > 0`。

### P0-2 抓帧耗时（不要修那个正则）

- 事实：`AdbControlUnitMgr::screencap` 不写 `LogFunc`（该函数内的 `LogWarn` 只带函数名，不带 leave 耗时）；`ControllerAgent::screencap()` 走 `post` 而不是 `post_notified`，pipeline 抓帧也没有 `Controller.Action` 事件。
- 所以 `timings.screenshot` 对 ADB 是**结构性不可得**；把正则改宽也不会出现数据。保持 `None`，不要填 0。
- 三个替代口径，交叉验证：
  1. **连接测速**：`speed_test` 的 `The fastest method is ... [cost=Nms]`（`capture_performance` 已解析），单样本、连接窗口；
  2. **外部采样**：开发用脚本对真实设备连续 `controller.post_screencap()` 50 次，记 P50/P90；走同一条 ADB 截图命令，代表性最好；
  3. **单帧节点残差**：选一个"无输入、首次命中、无 post_delay"的节点，`page_wait − 识别 [cost=] − 已知等待` ≈ 抓帧 + 固定开销。
- 补充：如果将来接 `MaaCustomController`，抓帧**有** leave 行（`[CustomControlUnitMgr.cpp][...][screencap(cv::Mat &)] | leave, Nms`），可按 `ControlUnitMgr.cpp` + `screencap` 匹配；ADB 路径没有。
- 验收：三种口径给出同一量级；报告写明"抓帧占比来自采样/残差"，不冒充原生计时。

### P0-3 一局取证报告（不变业务）

- 字段：`page_wait.count / mean / total`、识别 `[cost=]` 的 count/total/P50、`business_phase.labels`、`screenshot` 采样值、未知项清单。
- 不发布整链百分比；至少 3 组可比样本后才谈比例。
- 交付物：一个只读脚本（放 `scripts/`，默认不跑真实任务）+ 一份真实运行的报告片段。

---

## 4. P1：纯节奏与重复读取（低风险）

详见[局内计划 §3](LABYRINTH_PERFORMANCE_PLAN.md#3-l1纯节奏与重复读取低风险)。摘要：

| 编号 | 改动 | 保留的断言 | 估算收益 |
|---|---|---|---|
| L1-1 | 遗物校准首读可信且无变动时跳过第二次读 | 冲突/低置信仍双读；账本只写事实 | 2–4 min/局 |
| L1-2 | `locate` 的 `collect` 结果按帧哈希缓存，`preview` 同帧复用 | preview 仍用自己同帧的框；不一致必重算 | 1–2 min/局 |
| L1-3 | 轮询 `post_delay` 500 → 150–200 ms | 每轮仍是全新观测；预算不变 | 0.5–1.5 min/局 |
| L1-4 | 最终结算盲点改为"帧变化即推进" | 40 次上限、未知即停不变 | 20–40 s/局 |
| L1-5 | 300 ms 观测派发间隔 | 复核后基本不触发 | 0（不作为收益项） |

---

## 5. P2：收窄证据集（中等风险，收益最大）

原则：最小充分证据集 = 预期页锚点 + DATE/DISCONNECT 中断锚点 + 可覆盖该页的弹层 canary。逐调用点 A/B，不做全局改小。

| 编号 | 位置 | 现状 → 改为 |
|---|---|---|
| L2-1 | `RouteTargetRecognition` | 全 32 页 → map + 12 页弹层 canary；非 map 回退全量 |
| L2-2 | 遗物校准内嵌识别 | 同上；非 map 返回 `None`（零输入） |
| L2-3 | 点击 guard | 被操作页 kind + canary；`control_anchor` 仍显式读 |
| L2-4 | 战斗轮询 | 结果页集合 + 中断锚点，每 3–5 轮补一次全量 |
| L2-5 | 商店 capture | 18 个锚点按页面拆最小集（确认页 ≈6–8） |
| L2-6 | 地图滚动 guard | `scope=None` → `probe_kinds=('map',)` |

三个必须同批处理的坑（详见[局内计划 §4.1](LABYRINTH_PERFORMANCE_PLAN.md#41-必须同批处理的三个坑)）：

1. 弹层覆盖：只扫 map/本页会"透过弹层点击"，canary 不可省。
2. WIN 缺失的 Boss 结果页：补读条件要改成 `'boss_summary' in scope`，否则合法结果被判 `battle_result_unknown`。
3. `controls` 的跨规则依赖：如 `LINK_CHOICE_REMAINING` 曾由 `link_reward` 顺带读到；逐调用点 A/B 是硬性要求。

---

## 6. P3：结构改动（风险最高，最后做）

| 编号 | 改动 | 保留的断言 | 估算收益 |
|---|---|---|---|
| L3-1 | `locate` + `preview` 合并为"预览即决策" | 点击框来自同一帧；未命中不发输入 | ~3 min/局 |
| L3-2 | 每格决策 7–9 → 4–5 | 变动后必读；冲突仍双读 | 2–4 min/局 |
| L3-3 | 同帧复用：一次观测产出 state 供同一 tick 多决策共享 | 复用前校验帧哈希 | 3–8 min/局 |

`FrameCache` 已有实现但无调用者（[timing.py:89](../src/pcr_maa/labyrinth/timing.py#L89)）；项目里已有现成帧哈希（[scene.py:367](../src/pcr_maa/labyrinth/scene.py#L367)），L1-2/L3-3 直接接它。

---

## 7. 外部方案：迁移 / 不迁移

BAAS 是独立 Python + C++ 栈，不使用 MaaFramework；相似只在范式。

| 方向 | 判断 |
|---|---|
| 先验 ROI、最小证据集 | **迁移**（用于页面族；地图节点位置随机，保留搜索） |
| 一帧多判、跳过重复分类 | **迁移**（必须帧哈希校验，不学 BAAS 的无校验复用） |
| 廉价 RGB 前置门 | **迁移**（只做分流，不做最终判定） |
| 模板预载 | 已等价（Maa Resource 加载即常驻） |
| 抓帧换 scrcpy | **不迁移**（见 §2） |
| OCR 拆 C++ 服务 + HTTP + 共享内存 | **不迁移**（Maa OCR 已同进程） |
| 逐字符置信度过滤 | 不迁移（Maa 只给整词 score；本项目靠 `expected` + 未知即停） |
| 固定阈值 0.8 / `rgb_diff=20` | 不迁移（本项目按节点阈值） |
| YOLO 做页面导航 | 不需要（页面目标固定） |

---

## 8. 安全不变量（所有批次都必须保持）

- 不降低识别阈值，不删除同帧守卫，不改变"未知即停"。
- 零输入拒绝路径与 reason 不变；账本只写实际发生的事实。
- 不新增常驻监控或轮询抓屏；预览继续复用业务缓存。
- 复用缓存必须带帧身份校验（哈希或等价证据），不得返回过期帧。
- 只允许"减少每次判断要读取的证据"，不允许减少"判断必须成立的条件"。

---

## 9. 批次与提交策略

| 批次 | 内容 | 交付 |
|---|---|---|
| Batch 0 | P0：识别计时修复 + 平台夹具 + 抓帧采样脚本 + 一局报告 | 独立提交，不改业务 |
| Batch 1 | L1-2（collect 缓存）+ L2-6（滚动 guard scope） | 两项同主题，可分别回滚 |
| Batch 2 | L1-1、L1-3、L1-4 | 每项一提交 |
| Batch 3 | L2-1 … L2-5 | 逐调用点一提交，附 A/B |
| Batch 4 | L3-1 … L3-3 | 每项一提交，最后做 |
| 评估线 | EmulatorExtras 只读 A/B | 独立分支；P0 显示抓帧 <10% 则关闭 |

每项保留回退开关（常量或配置），默认行为不变；单项 A/B 或反例失败直接回退，不牵连其他项。

---

## 10. 验收

- 离线 A/B：`tests/data` 每张真实帧，旧/新 scope 比较 `kind`、`area`、`node_type`、业务依赖的 `controls`、拒绝路径与 reason。
- 必需反例：错页、地图上叠弹层、WIN 缺失的 Boss 结果页、DATE/DISCONNECT、战斗轮询意外页、遗物首读冲突。
- 回归：`pytest -k labyrinth`；重点专项见[局内计划 §7.3](LABYRINTH_PERFORMANCE_PLAN.md#73-回归命令)。
- 公开回归：`python -X utf8 -B scripts/verify.py public`。
- 实机：同设备、同设置、同难度，改前/改后各 ≥3 组；对比 `events.jsonl` 的 `business_phase.labels` 与 `page_wait.count/mean`，确认业务结果与拒绝路径一致。
- 未满 3 组只报"调用数与阶段耗时趋势"，不报整链百分比。

---

## 11. 未验证与风险

1. 抓帧占比仍是未知项，§3 的 P0 才能给出可信数字。
2. 局内分钟数均为"次数 × 单次成本"的推断，未在用户 Windows 真机采集整局样本。
3. BAAS 的 5–20/17/300 ms 是它自己的环境与文档数字，不能直接搬到本设备。
4. EmulatorExtras 的 package/display/input 身份证明尚未建立；只读取帧不等于可正式输入。
5. 弹层 canary 的完备性未证明；遗漏即可能"透过弹层点击"。
6. `controls` 的跨规则依赖可能还有未发现项。
7. L2-4 会改变 `global_scene_complete` 语义，现有性能用例需同步，不能当"只改参数"。

---

## 12. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-24 | 初稿：汇总 BAAS/scrcpy 评估；明确 P0 度量修复（含 ADB 抓帧无原生耗时行的更正）、L1/L2/L3 批次与验收；并入 `LABYRINTH_PERFORMANCE_PLAN.md` 作为局内细项 |
