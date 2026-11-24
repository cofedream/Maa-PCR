# 黎明界局内耗时记录与 L1/L2/L3 优化计划

状态：计划，未实施。本文记录只读分析结论与三层优化方案，基于 R77 / v0.1.30 源码和公开测试帧；2026-11-24 并入 `perf-optimization-plan` 实施基线时仓库已到 v0.2.3，**文中行号以当时版本为准，实施前须按当前源码复核**。它不构成实机验收，也不声明整链提速；任何百分比都必须按[开发指南](DEVELOPMENT.md)的要求，在同一设备、相同设置下至少采集 3 组可比样本后才能给出。

边界：进入黎明界、初始地图可操作之后，到最终通关或回到主页为止。**不含**刷开局、登录、启动、协议建局、账本恢复。默认按难度 5 记录（35 节点 / 约 12–14 场战斗）；难度 1 为 20 节点 / 约 7 场战斗，量级不同，不能套用本文的分钟数。

非目标：不降低识别阈值、不删除同帧守卫、不改变未知结果不停止的规则、不为取证新增常驻监控或轮询抓屏。优化只允许"减少每次判断要读取的证据"，不允许减少"判断必须成立的条件"。

---

## 1. 计量口径与证据

### 1.1 代码侧口径

- `Timings` 的 8 个类别（`screenshot / recognition / strategy / input / swipe / page_wait / protocol_request / business_phase`）互相包含，**不能相加**（[timing.py:1-8](../src/pcr_maa/labyrinth/timing.py#L1-L8)）。
- `business_phase` 的 label 是阶段墙钟，最接近"一小时花在哪"：`opening / handoff / playing / moving / battle / shop / reward / team / recovering / retiring`。注意战斗期间代码不发 progress 事件，**战斗动画与轮询时间会被归入进入战斗前的那一档（通常 `team`）**。
- `page_wait` 是每次 `native.run` 的整段等待（含截图、识别、输入与 500ms 轮询），其 `count` 就是一局的原生任务次数。
- 每局正常结束会在 `.runtime/labyrinth-work/ledger/runs/<run_id>/events.jsonl` 的 `finished` 行写入 `timings`（[records.py](../src/pcr_maa/labyrinth/records.py)、[batch.py:408-411](../src/pcr_maa/labyrinth/batch.py#L408-L411)）；渠道服在 `ledger/servers/<server>/...`。
- 注意：`timing.py:201-202` 的私有 trace 正则与真实日志不一致。MaaUtils 的函数名宏在 **MSVC** 下是 `__FUNCTION__`（类限定、无参数，形如 `[OCRer::analyze]` / `[AdbControlUnitMgr::screencap]`），在 **GCC/Clang** 下是 `__PRETTY_FUNCTION__`（`[analyze()]` / `[screencap(cv::Mat &)]`），两种构建都不匹配。更关键的是 `AdbControlUnitMgr::screencap` 并不写 `LogFunc`，ADB 抓帧没有原生 leave 耗时行；因此 `timings.screenshot` 对 ADB **结构性不可得**，`timings.recognition` 则可通过按 `OCRer.cpp` 等文件匹配 `[cost=]` 修复。抓帧耗时的替代口径见[优化计划](OPTIMIZATION_PLAN.md)的 P0。

### 1.2 本机离线实测

方法：Linux + maafw 5.13.0，`SavedImages` 回放 `tests/data` 公开原帧；每场景预热后重复采样；子识别次数从 `maafw.log` 的 `VisionNS::(OCRer|TemplateMatcher)::analyze()` 行计数，墙钟用 `perf_counter`。

限制：不是 Windows、不是真机、不包含 ADB 截图与游戏动画；墙钟只代表本机，**子识别次数与硬件无关**。本文把它当作"每次判断要读多少证据"的度量，而不是用户机器的绝对耗时。

### 1.3 仓库内已有实测（可引用）

| 数值 | 含义 | 出处 |
|---|---|---|
| 12.096 s / 123 次子识别 ≈ 98 ms/次 | 用户反馈日志里一次全场景识别的实际耗时 | [RELEASE_STATUS.md:273](RELEASE_STATUS.md#L273) |
| ~51 ms/节点 | Maa 节点级固定开销；OCR 8–9 ms/词、图标 <1 ms/个 | [LABYRINTH_INTEGRATION.md:162](LABYRINTH_INTEGRATION.md#L162) |
| P50 160–186 ms | 三节点原生读取场景的实测读取耗时 | [R53_PERFORMANCE.md:9](R53_PERFORMANCE.md#L9) |
| 10.91 s / 15.06 s | 179.7 MB 账本上一次 `events()` 深拷贝 / 每局首次全量读+校验 | [LABYRINTH_INTEGRATION.md:81](LABYRINTH_INTEGRATION.md#L81) |
| 52.923 s | 真机"点开始→ready"（含人工验证码，R44），不在本文边界内 | [RELEASE_STATUS.md:369](RELEASE_STATUS.md#L369) |
| 11:06.685 | 正式 R52 自然日常整轮，非黎明界 | [R53_PERFORMANCE.md:46](R53_PERFORMANCE.md#L46) |

白名单：上表数字、"全规则 32 页 / 全场景 123 次子识别"的静态计数、场景观测预算 20s、轮询固定 500ms、最终结算 1.5s×40 上限。
灰名单：一切"次数×单次成本"换算出的秒数（本机与用户机差 3–6 倍）、战斗动画时长、实际每局战斗数与真实路线组成。

### 1.4 本机实测的 scope 矩阵（真实帧）

| scope | 地图帧 | 商店帧 | 遗物奖励帧 |
|---|---|---|---|
| `scope=None` 全场景（123 次） | 123 次 / 1.84–2.16 s | 123 / 4.0 s | 123 / 4.1 s |
| **全 32 页规则（当前热路径）** | **40 次 / 0.86–1.06 s** | **42 / 2.5 s** | **38 / 2.2 s** |
| map + 12 页弹层 canary | 26 次 / 0.55–0.66 s | — | — |
| 只有 map | 14 次 / 0.15–0.20 s | — | — |
| 单页 kind（guard 用；地图帧按 map scope） | 14 次 / 0.15–0.20 s | 13 / 0.50 s | 13 / 0.15 s |

同一帧的 `NodePixels.collect`（16 模板、4 线程、纯 CPU）本机实测 **1.17–1.28 s/次**；单 OCR 裁剪约 66 ms，单模板约 1 ms。

---

## 2. 局内成本结构

### 2.1 现状：不是全场景，但也不是"只扫预期页"

局内有三种扫描，不能混为一谈：

1. **全局状态判定**：地图定位/预览（[autoplay.py:589-627](../src/pcr_maa/labyrinth/autoplay.py#L589-L627)）、遗物校准内嵌识别（[relics.py:1086-1107](../src/pcr_maa/labyrinth/relics.py#L1086-L1107)）、战斗轮询（[autoplay.py:881-923](../src/pcr_maa/labyrinth/autoplay.py#L881-L923)）、`_observe`（[autoplay.py:971-979](../src/pcr_maa/labyrinth/autoplay.py#L971-L979)）。这些当前传**全 32 页规则**（38–42 次子识别）。作者注释写明目的：「retaining every overlay that can cover MAP」——即保留"可能盖在地图上的弹层"。
2. **预期页读回**：`wait_for` 只扫 `allowed ∪ sources` 加上 `_probe_scope` 强制并入的 5 条基础规则（battle/team/map/move_confirmation/home，[scene.py:212-215](../src/pcr_maa/labyrinth/scene.py#L212-L215)），通常 9–28 次。
3. **全场景 `scope=None`**（123 次）：只出现在地图滚动 guard（[autoplay.py:673-674](../src/pcr_maa/labyrinth/autoplay.py#L673-L674) 是唯一漏传 `probe_kinds` 的 custom recognition）和定位发现异常页时的回退（[autoplay.py:601-606](../src/pcr_maa/labyrinth/autoplay.py#L601-L606)）。

所以优化空间不在"把 123 砍成 10"，而在**把第 1 类的 32 页收窄成"预期页 + 中断锚点 + 弹层 canary"**，以及把第 3 类限制在真正异常的分支。

### 2.2 每个地图格的 7–9 个原生任务

| 任务 | 内容 | 是否必需 |
|---|---|---|
| 遗物校准读 ×2 | 每次内部再跑一次全 32 页场景识别 + ≤5 个放大数字 OCR + 5 个模板计数；两次结果取一致（[autoplay.py:721-739](../src/pcr_maa/labyrinth/autoplay.py#L721-L739)、[relics.py:1047-1052](../src/pcr_maa/labyrinth/relics.py#L1047-L1052)） | 双读一致性是安全项；**第二次读可条件化** |
| `locate` | 全 32 页识别 + `collect`，判断目标是否可见 | 决策需要；32 页分类过度 |
| `preview` | 再一次全 32 页识别 + 再一次 `collect`，命中的框就是点击目标 | 点击同帧守卫必需；32 页分类过度 |
| 转场读回 1–2 次 | 每次轮询 = 一帧观测 + 500ms 停顿 | 必需 |
| `confirm_move` guard + 点击 | 同帧"页面 kind + 控件锚点 + 中断锚点"校验 | 必需 |
| 到达读回 1–2 次 | 同上 | 必需 |

关键事实：**同一个静态画面在一个节点内被重新分类 4–6 次**（校准 2 + 定位/预览 2 + guard 1 + 读回若干），而且没有任何跨决策的同帧复用（`FrameCache` 已实现但无调用者，[timing.py:89-101](../src/pcr_maa/labyrinth/timing.py#L89-L101)）。

### 2.3 每场战斗的 18–25 个原生任务（不含轮询）

挑战页点击与等待 2–3；编组页稳定读 2–4 帧（每次回调 P50 277–507 ms）；改队伍时每换 1 人 = seek 滚动 + 选择 + 稳定读回；开战前逐队读回 + 开战点击 2–4；胜利补观察 1；结算/奖励读回链 6–12。战斗期间另有 300s 预算内的持续轮询（每轮 = 一次全规则观测 + 500ms 停顿），与动画并行但持续占用 ADB 截图与 OCR。

### 2.4 一局规模与时间分解（推断）

| 项目 | 难度 1 | 难度 5 |
|---|---:|---:|
| 路线节点 | 20 | 35 |
| 战斗（模板口径） | 约 7 | 约 12–14 |
| 原生任务总数 | 350–550 | 700–1000 |
| 每节点任务 | 7–9 | 7–9 |

按用户机 98 ms/子识别换算：全规则观测约 3.5–4.5 s、单页 kind 约 1.4–2 s、全场景约 12–20 s。据此，难度 5 一局大致是：**战斗动画 10–20 min（不可压）+ 识别/决策 25–35 min（可压）+ 纯节奏 1–3 min（可压）+ 结算/重试 2–8 min**，合计约 40–65 min。该分解为推断，必须用 §7 的实机 A/B 核对。

---

## 3. L1：纯节奏与重复读取（低风险）

原则：不改变任何识别内容，只去掉没有信息增量的读取与纯节奏。

| 编号 | 现状 | 改动 | 保留的安全断言 | 估算收益 | 风险 |
|---|---|---|---|---|---|
| L1-1 | 遗物校准每格固定 2 次读（[autoplay.py:721-739](../src/pcr_maa/labyrinth/autoplay.py#L721-L739)） | 首读可信、且自上次读以来没有新增遗物/购买/换区时，跳过第二次读；否则仍双读 | 冲突/低置信仍双读；`map_relic_consensus` 语义不变；账本仍只写实际事实 | 每局约 2–4 min | 低 |
| L1-2 | `collect` 每格 2 次（locate + preview） | 缓存 locate 的 `collect` 结果；preview 前先比对两帧像素哈希，一致才复用，否则重算 | preview 仍使用自己同帧的目标框；不一致必重算 | 每局约 1–2 min | 低 |
| L1-3 | 轮询固定 `post_delay=500ms`（[autoplay.py:66-73](../src/pcr_maa/labyrinth/autoplay.py#L66-L73)） | 降到 150–200ms | 每轮仍是全新观测；超时预算不变 | 每局约 0.5–1.5 min | 低，需核对 `tests/test_public_pipeline_delays.py` 与场景预算用例 |
| L1-4 | 最终结算未知页每 1.5 s 盲点一次，上限 40 次（[scene.py:543-559](../src/pcr_maa/labyrinth/scene.py#L543-L559)） | 改成"帧像素变化即推进"，保留 40 次上限与未知即停 | 每轮仍重新观测；未知仍不停发新操作 | 每局约 20–40 s | 中低 |
| L1-5 | 300ms 观测派发间隔（[owned_runtime.py:150-174](../src/pcr_maa/labyrinth/owned_runtime.py#L150-L174)） | 复核后确认基本不触发（观测本身 1–3 s），不作为收益项 | — | 0 | — |

L1 合计约 **4–7 分钟/局**（推断）。

---

## 4. L2：收窄证据集（中等风险，收益最大）

原则：**最小充分证据集 = 预期页锚点 + DATE/DISCONNECT 中断锚点 + 可覆盖该页的弹层 canary**。`_probe_scope` 已强制并入 5 条基础规则，且 scoped 分支总会读 DATE/DISCONNECT（[scene.py:316-318](../src/pcr_maa/labyrinth/scene.py#L316-L318)），所以"中断检测"不会因为收窄而丢失。判定、拒绝、同帧语义都不变；不匹配时保留现有全量回退或零输入拒绝。

| 编号 | 位置 | 现状 | 改为 | 实测依据 |
|---|---|---|---|---|
| L2-1 | `RouteTargetRecognition.analyze`（[autoplay.py:596-606](../src/pcr_maa/labyrinth/autoplay.py#L596-L606)） | 全 32 页 | map + 弹层 canary（12 页，实测约 26 次子识别）；非 map 仍回退 `scope=None` 全量 | 26 次 vs 40 次；地图帧 kind/area 三种 scope 一致 |
| L2-2 | 遗物校准内嵌识别（[relics.py:1096-1100](../src/pcr_maa/labyrinth/relics.py#L1096-L1100)） | 全 32 页 | 同上；非 map 返回 None（零输入，和现在一致） | 同上 |
| L2-3 | 点击 guard（[scene.py:444-465](../src/pcr_maa/labyrinth/scene.py#L444-L465)） | 沿用观测时的 scope（常为 32 页），等于每次点击再跑一次全量分类 | 被操作页 kind + 弹层 canary；`control_anchor` 仍显式读取 | 真实页 A/B：shop 42→13 次、shop_confirmation 40→12、item_reward 38→16、free_role_reward 38→15，kind 全部一致 |
| L2-4 | 战斗轮询（[autoplay.py:892](../src/pcr_maa/labyrinth/autoplay.py#L892)） | 每轮全 32 页 | 结果页集合 + 中断锚点，每 3–5 轮补一次全量 | 见下方"必须同批处理的坑" |
| L2-5 | 商店 capture（[shop_execution.py:74-88](../src/pcr_maa/labyrinth/shop_execution.py#L74-L88)） | 每帧固定 18 个锚点 | 按页面拆最小集（确认页 ≈6–8 个） | 每笔购买 7 个 capture，可省 40–50% 商店 OCR |
| L2-6 | 地图滚动 guard（[autoplay.py:673-674](../src/pcr_maa/labyrinth/autoplay.py#L673-L674)） | `scope=None`（123 次） | `probe_kinds=('map',)`（≈14 次）；非 map 时 `hit=None`，不发 Swipe | 唯一漏传 `probe_kinds` 的 custom recognition |

### 4.1 必须同批处理的三个坑

1. **弹层覆盖**：地图/操作页上可能叠结算、宝箱、移动确认等弹层。只扫 map 或只扫本页会"透过弹层点击"，所以合并版必须带弹层 canary；单页 kind 只能用于"页面本身 + canary"。
2. **WIN 缺失的 Boss 结果页**：`analyze` 只在 `set(scope)==set(_RULES) and kind=='unknown'` 时补读 `NEXT / BOSS_SUMMARY_NEXT / BATTLE_RESULT_NEXT`（[scene.py:330-332](../src/pcr_maa/labyrinth/scene.py#L330-L332)）。收窄战斗轮询后这个条件不再成立，必须改成 `'boss_summary' in scope`（或显式加入这三个锚点），否则合法的 Boss 结果会被判 `battle_result_unknown`。
3. **`controls` 的跨规则依赖**：实测发现 relic_reward 用单页 scope 时 `controls` 少了 `LINK_CHOICE_REMAINING`，因为该锚点以前是经 `link_reward` 规则顺带读到的。因此 L2 必须逐调用点做 A/B，不能全局改小 scope；业务依赖的 anchor 要显式加入最小集。

### 4.2 估算收益

L2 各项合计约 **5–9 分钟/局**（推断）：L2-1 约 1.5–2 min、L2-2 约 1.5–2 min、L2-3 约 1.5–2.5 min、L2-5 约 0.5–1.5 min、L2-6 0–1 min、L2-4 墙钟 0–2 min（主要收益是战斗期间 CPU/截图占用与结束检测延迟）。战斗轮询与动画重叠，不能按"每次省下的秒数×轮询次数"直接换算成墙钟。

---

## 5. L3：结构改动（风险最高）

| 编号 | 改动 | 保留的断言 | 估算收益 |
|---|---|---|---|
| L3-1 | `locate` + `preview` 合并为"预览即决策"：预览命中即点击（点击框来自同一帧），未命中再 `locate`/滚动 | 点击仍由同帧识别框决定；未命中不发输入 | 每局约 3 min |
| L3-2 | 每格决策数 7–9 → 4–5：遗物校准只在"上次读之后有遗物变动/换区/首节点"执行；合并部分读回 | 变动后必读；冲突仍双读 | 2–4 min |
| L3-3 | 同帧复用：一次观测产出 state，供同一 tick 的多决策共享；`FrameCache` 已有实现但未使用 | 复用前校验帧像素 hash 一致；不一致重算 | 3–8 min |

L3 合计约 **8–15 分钟/局**（推断），需要完整回归与原帧回放，建议在 L1+L2 实机对照之后再动。

---

## 6. 收益汇总与地板

| 阶段 | 难度 5 一局（用户机，推断） |
|---|---|
| 现状 | 约 40–65 min |
| L1 | 约 35–60 min |
| L1 + L2 | 约 32–52 min |
| L1 + L2 + L3 | 约 28–42 min |
| 地板 | **约 25–30 min**（战斗动画 10–20 min + 每步最少一次同帧证据与结算/重试） |

这些是推断区间，不是实测；按[开发指南](DEVELOPMENT.md)的要求，至少 3 组可比样本前不发布整链百分比。若实际战斗动画明显短于 10 分钟，则代码侧占比更高、优化收益更大；反之优化天花板更低。

---

## 7. 验证与验收计划

### 7.1 离线 A/B（每项改动必做）

- 对 `tests/data` 每张真实帧，旧 scope / 新 scope 各跑一次识别，比较：`kind`、`payload.area`、`payload.node_type`、业务依赖的 `controls` 键、拒绝路径（是否零输入、reason 是否不变）。
- 同时统计 `maafw.log` 的 `VisionNS` 行数与 `[cost=]` 总和，确认调用数下降目标（如 40→26、42→13）。
- 地图帧额外断言三种 scope 下 `kind` 与 `area` 一致（本机已验证 3 张地图帧）。

### 7.2 必须新增的反例测试

- 错页帧：locate/preview/guard 零输入且 reason 不变。
- 地图上叠结算/宝箱/移动确认弹层：不得被当作纯地图点击。
- WIN 缺失的 Boss 结果页：仍能走 `_owned_boss_summary_scene`。
- DATE / DISCONNECT 帧：仍停止。
- 战斗轮询遇到意外页：仍能在有界轮次内停止，不得空转到 300s。
- 遗物校准首读冲突/低置信：仍双读。

### 7.3 回归命令

- `pytest -k labyrinth`（约 1900 项）。
- 重点专项：`tests/test_labyrinth_scene.py`、`tests/test_labyrinth_scene_budgets.py`、`tests/test_labyrinth_autoplay.py`、`tests/test_labyrinth_battle_recognition_performance.py`（其中 `global_scene_complete` 断言会随 L2-4 同步调整）、`tests/test_labyrinth_control_anchor120.py`、`tests/test_labyrinth_node_pixels.py`、`tests/test_labyrinth_relic_icon_guard_frame.py`、`tests/test_labyrinth_shop_execution.py`。
- 公开回归：`python -X utf8 -B scripts/verify.py public`。

### 7.4 实机 A/B

- 同一设备、同一设置、同一难度，改前/改后各至少 3 组可比样本；注明冷启动或预热条件，保留慢样本。
- 对比 `events.jsonl` 的 `business_phase.labels`（`moving` / `team` / `reward` / `shop`）与 `page_wait.count/mean`；同时确认业务结果、拒绝路径、未知即停完全一致。
- 未达到 3 组样本前只报告"识别调用数与阶段耗时趋势"，不报告整链提速百分比。

### 7.5 回滚

每项改动独立提交，保留"回退到全量 scope / 原轮询值"的开关（常量或配置），默认行为不变；单项无法通过 A/B 或反例时直接回退，不影响其余项。

---

## 8. 未验证与风险清单

1. 本文所有分钟数均为"次数×单次成本"的推断，未在用户 Windows 机、真机上采集整局样本。
2. 本机 scope 矩阵的墙钟只代表 Linux 测试机；用户机按 98 ms/子识别约慢 3–6 倍，但用户机截图/CPU 竞争情况未知。
3. 战斗动画时长、每局实际战斗数与路线组成无仓库记录，需用户日志确认。
4. 弹层 canary 集合的完备性未证明；遗漏的弹层可能导致"透过弹层点击"，必须由 §7.2 反例覆盖。
5. `controls` 的跨规则依赖可能还有其他未发现项；逐调用点 A/B 是硬性要求。
6. L2-4 的战斗轮询改动会改变 `global_scene_complete` 语义，现有性能用例需要同步更新，不能把它当成"只改参数"。
7. 账本恢复与启动耗时不在本文边界内；账本 `events()` 的 10.91 s/15.06 s 属于每局开始前的固定项，若用户从"点开始"计时则会看到它，需单独归类。

---

## 9. 关联文档

- [优化总纲](OPTIMIZATION_PLAN.md)（本轮结论、P0 度量修复与执行批次）
- [开发指南](DEVELOPMENT.md)（性能样本要求、检查命令）
- [黎明界集成](LABYRINTH_INTEGRATION.md)（安全设计与历史事故记录）
- [R53 综合提速测量](R53_PERFORMANCE.md)（局部性能证据与不可相加说明）
- [验收范围](RELEASE_STATUS.md)（用户反馈日志中的 12.096 s 记录）

## 10. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-11 | 初稿：记录局内耗时结构、scope 矩阵实测、L1/L2/L3 方案与验证计划（未实施） |
| 2026-11-24 | 并入 `perf-optimization-plan` 实施基线：修正函数名宏的平台差异与 12.096 s 出处；P0 度量修复先于 L1，详见[优化总纲](OPTIMIZATION_PLAN.md) |
