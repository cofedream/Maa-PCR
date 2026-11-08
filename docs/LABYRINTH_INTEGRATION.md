## 2026-11-08 商店遗物身份接入静态图鉴（完整购买序列原帧）

背景：商店三格遗物此前只有 OCR 名称/属性身份（`visible-shop-item:<名称 sha256>`），没有图鉴身份；购买确认只用点击帧捕获图标做同 session 模板一致性校验。奖励三选一页已以图鉴图标为主身份，商店是最后一个 name-only 遗物入口。

实机抓帧（2026-11-08 手动走到商店，随后手动购买一次）：`tests/data/labyrinth-shop-relics-20261108.png`（货架三件可购买遗物）、`…-confirm-20261108.png`（与 slot 2 配对的购买确认）、`…-complete-20261108.png`（购买完毕）、`…-after-20261108.png`（购买后同页：slot 0 当前 3、slot 1 当前 1、slot 2 变为职能印记，深渊蚀杖诅咒在底行已售罄）。manifest 记录 sha256、1280×720、来源/用途/期望值；画面不含玩家名、账号、设备或运行标识。

改动：
- `relics.relic_catalog(tasker)` 暴露运行内唯一已验证图鉴包（缺包返回 None，不重复加载）。
- `ShopRecognition` 新增 `catalog`：`state=='shop'` 时对三格 `SHOP_ICON_ROIS` 做图鉴匹配；可信则写入 `catalog_id`/`catalog_name`/`identity_source='static_icon'`/`catalog_match`，并与同帧属性行 OCR 的 mark/bonus 交叉校验；冲突时返回 `shop_relic_identity_conflict`（整帧 unknown，停止而不是猜）；图标不可信或包缺失保持原 OCR 路径。
- 购买确认：confirmation 帧对 `SHOP_DIALOG_ICON_ROI` 做图鉴匹配，payload 增加 `dialog_catalog`；`_item_identity` 新增 `expected_catalog_id`，命中返回 `native_observed_catalog`（图片证据优先于精确文本；捕获图标模板 `native_observed_icon` 仍是图鉴不可用时的图片兜底）；`MaaShop.run/purchase_once/purchase_readback/_input` 透传 `catalog_id` 与 identity_method。
- 账本/策略：item 增加 catalog 字段，`item_id` 仍保留可见身份，不改写既有账本语义。

验证（离线，真实帧）：pre 帧三格 trusted **12003/14003/32002**（score .90/.91/.88，margin .27/.39/.35），OCR 名称/价格/当前层数（0/1/0）与卡面一致；确认帧 `selected_icon_verified=true`、`dialog_catalog.id=32002`、价格 2850、系列「削弱+3/当前 0」与货架属性行一致；购买完毕 `2850`/深渊蚀杖诅咒；购买后余额 2735，slot 0 当前 3、slot 1 当前 1、slot 2 职能印记，底行已售罄的 32002 仍可被图鉴命中。合成帧反例：把 14003（强化+1）图标贴到 slot 2（卡面 削弱+3）→ 图鉴与属性行冲突，`_capture` 以 `shop_readback_unknown` 停止、零输入；抹掉图标 → 图鉴不可信，回退 OCR 文本路径；`relic_catalog` 返回 None → 同样回退。新增 `tests/test_labyrinth_shop_relic_identity.py`（6 项，含完整购买序列与反例）。`pytest -k shop` `238 passed / 28 skipped`；`pytest -k labyrinth` `1888 passed / 123 skipped / 5982 deselected`（18 subtests，4:28）。

未做/边界：本局未实机复跑；商店 `item_id` 仍是可见身份（未改为 catalog id）；确认弹窗的 catalog 证据要求图鉴可信，不可信时仍用捕获图标模板/精确 OCR；商店以外的 name-only 路径（地图/物品读取、链接/角色奖励）未并入；阈值、ROI、图标包与 masterdata 未改。

## 2026-11-08 账本查询改为只读视图（奖励选择不再深拷贝整本账本）

问题：`Batch.run` 整局持有 journal 锁（[batch.py:330](src/pcr_maa/labyrinth/batch.py#L330)），锁内 `Journal.events()` 为兼容历史调用方返回 `deepcopy(cache)`（[preset_execution.py:214](src/pcr_maa/preset_execution.py#L214)）。账本 `transactions.jsonl` 已 179.7 MB / 10,918 行（每条内嵌完整 native 回执，平均约 16 KB/行），本机实测：锁内一次 `events()` = **10.91 s**，每局首次全量读+逐条校验 = **15.06 s**；而 `last_event()` = **0.2 ms**，在缓存上做一次只读条件扫描 = **1.4 ms**。日志里 `[15:13:59] 读卡完成 → [15:14:09] 图鉴身份完成`、`[15:08:32] 角色奖励页 → [15:08:44] 选角已发出` 的 10–12 秒空档都是整本复制，与 ADB 截图无关。

改动：`Journal.any_event(predicate)` 新增只读扫描（不 deepcopy、不暴露可变副本；predicate 内不得修改或保留事件字典）；`events()` 语义保留给确实需要独立副本的启动校验/恢复；`last_event()` 继续用于“只看最后一条”。替换热路径：

- 遗物选择重复检查 [relics.py:862](src/pcr_maa/labyrinth/relics.py#L862)
- 角色奖励重复检查（event-free 与普通两条）[rewards.py:120](src/pcr_maa/labyrinth/rewards.py#L120)、[rewards.py:196](src/pcr_maa/labyrinth/rewards.py#L196)
- 连结印记重复检查与 in-flight 检查 [links.py:327](src/pcr_maa/labyrinth/links.py#L327)
- 商店购买校验 [shop_execution.py:431](src/pcr_maa/labyrinth/shop_execution.py#L431)（原来为取 `events[-1]` 复制整本）
- 旧局/开局/会话校验 [game_port.py:259](src/pcr_maa/labyrinth/game_port.py#L259)、[game_session.py:465](src/pcr_maa/labyrinth/game_session.py#L465)、[acquisition.py:203](src/pcr_maa/labyrinth/acquisition.py#L203)、[acquisition.py:212](src/pcr_maa/labyrinth/acquisition.py#L212)（最后一条改 `last_event()`，存在性改 `any_event()`）

业务判定、账本格式、fsync、未知结果不重发规则均不变；只改读取方式。

验证（同一本 179.7 MB 账本、本机）：`any_event` 扫描 1.4 ms vs `events()` 10.91 s vs `last_event()` 0.2 ms。`tests/test_journal_lock_cache.py` 新增 4 项：锁内 `any_event` 不触发 `deepcopy`（monkeypatch 计数）、锁外读文件、append 后可见、外部改动仍按原样拒绝；`test_labyrinth_relic_identity.py::test_choose_guards_with_the_trusted_catalog_identity` 现在把 `Journal.events` 打成失败，遗物选择仍正常（防止热路径回退到整本复制）。目标套件（journal 缓存、遗物帧/身份、free role、independent edges、reward/shop/event 上游、shop execution、links）`356 passed / 38 skipped`；`tests/test_preset_execution.py tests/test_preset_result_reconcile.py tests/test_guide_control_check.py tests/test_abyss.py tests/test_abyss_reconcile.py` 共 `234 passed / 35 skipped`；`pytest -k labyrinth` `1882 passed / 123 skipped / 5982 deselected`（18 subtests，4:22）。

未做/边界：每局启动仍会做一次全量读+逐条校验（约 15 s），且 `owned_runtime` 的 `pending_intent` 与 `Batch.run` 在两次加锁中各读一次（合计约 30 s/局）；下一步可用 sidecar 记录已校验 offset/stamp 只校验新增行，或跨锁复用已验证缓存（本轮未做，避免改动校验语义）。账本体积本身（每条内嵌完整回执）未处理。日常/公会战共用 `Journal` 类，`events()` 行为未变。

## 2026-11-08 遗物点击守卫改为同帧图鉴身份（OCR 丢字不再拦截）

实机失败记录 player-log run `cb977ae3ac554d2da0f01ce814e72313`（业务 run `489c2c929c68495ab0163ba396239c0d`，`enter_id=13475`，`reward_id=20701:relic_reward:1`）：15:13:58 进入第四次三选一遗物页，选择 slot 0「杀戮之矛」（`relic_id=13001`，会心+1，当前 4）。15:14:09 账本 `relic_reward_select` 已写入意图，15:14:13 以 `native_input_not_sent` 停止（`relics.py:851`、`relics.py:807`、`native.py:340`），进度 1:0，没有发出点击。

原因：卡面实际显示「杀戮之矛」，但原生 OCR 在该帧把名称读成「杀之矛」——复杂字形「戮」被检测器漏掉（在同一帧重放 offer 读取也得到「杀之矛」，属该卡面的稳定漏检）。13:51 的 `8f1a483` 把 fresh guard 的名称来源换成图鉴规范名且明确不加字符容错，于是守卫期望 `^\s*杀\s*[·・‧•]*\s*戮\s*...\s*矛\s*$`，点击帧不命中，`LabyRelicSelectOnce` 2.5 秒超时，没有点击也没有误操作。图鉴身份本身是正确的（静态图标 13001，离线复算 score .8987 / rival margin .2699），失败只发生在「用 OCR 名称做点击身份」这一步。

修复：可信图鉴身份改为「同帧图标 id」判定，OCR 名称不再参与点击成立。`RelicStaticIconCapture` 新增 `guard` 模式：只匹配目标 slot 的 `ICON_ROIS[slot]`，`trusted` 且 catalog id 等于意图 `relic_id` 才返回 hit，否则 `box=None`（不点击；异常、缺包、坏帧同样不命中）；`relic_pipeline` 新增 `LabyRelicGuardIcon{slot}0` 占位节点（未提供 slot/relic_id 时恒不命中），`selection_override` 在 `offer.identity` 可信时用该图标守卫替换 `LabyRelicGuardName{slot}0`；图标不可信或包缺失时保持原精确（分隔点可选）OCR 名称守卫。属性行、`还剩N次`、按钮、Bright、`LabyRelicSelectedIconCapture` 与点击坐标均不变。同帧图标匹配单槽约 13 ms。

验证（离线）：在实机失败原帧上，`LabyRelicObserved` 仍读出 `杀之矛/再生之手镯/暗至宝圣物`，静态图标识别 13001/15005/22001；新守卫在 slot 0 命中 `LabyRelicGuardIcon00` 并点击 `[239,614]`（落在按钮 ROI），原生回执 `action_started=true`；把意图换成 `13002` 后同一帧不命中、零点击、`require_native_input` 判为 `NativeInputNotSent`。新增公开测试资源 `tests/data/labyrinth-relic-ocr-drop-20261108.png` 与同名 `.json`（sha256、1280×720、来源用途；画面不含玩家名、账号、设备或运行标识），新增 `tests/test_labyrinth_relic_icon_guard_frame.py`。更新 `test_labyrinth_relic_offer_frame.py`（图鉴身份断言改为同帧图标守卫）、`test_labyrinth_relic_name_separator.py`（身份守卫使用图鉴图标、文本路径保留）、`test_labyrinth_relic_identity.py`（guard 模式命中/拒绝/缺包/坏帧与 pipeline 节点）。目标文件 `45 passed / 1 skipped`；遗物、链接、商店与奖励相关套件 `293 passed / 25 skipped`；`pytest -k labyrinth` `1882 passed / 123 skipped / 5979 deselected`（18 subtests，3:33）；识别审计/像素策略/回调/延迟套件 `72 passed / 29 skipped`。

未做/边界：本局未实机复跑；守卫改为图标后，若点击帧图标低于图鉴可信阈值（遮挡/动画）会按「未发出输入」安全停止，不再回退 OCR 名称；offer 帧与点击帧若被图鉴以同一错误 id 命中，守卫会跟随同一误判（属性行仍与图鉴 mark/bonus 交叉校验）；商店 relic、地图/物品读取和链接/角色奖励的 name-only 守卫未并入本模块；阈值、ROI、超时、图标包与 masterdata 未改。

## 2026-11-08 奖励页遗物身份收束到 RelicOffer.identity（离线原帧复现）

实机失败记录 player-log run `65bdcc4e34b74c0b9d89707782ea4f74`（业务 run `11af6eafef224049ae87e287b82d8a58`，`enter_id=13473`，`reward_id=50501:relic_reward:1`）：11:17:18 进入第二次三选一遗物页，选择 slot 1「白光辉铠·断壁」（`relic_id=35003`，守备+3）。11:17:34 账本 `relic_reward_select` 已写入意图，11:17:37 以 `native_input_not_sent` 停止（`relics.py:795`、`relics.py:751`、`native.py:340`），进度 1:2，实际没有发出点击，游戏停留在原选择页。

原因：观测帧把卡面名称读成 `自光辉铠` + `·断壁`（`白` 误读为 `自`），静态图鉴已可信识别为 35003 并把规范名 `白光辉铠·断壁` 写入账本；但 `MaaRelicReward.choose` 的 fresh guard 仍调用 `selection_guard(self.tasker, offers, selected.slot, icon=True)`，传的是 OCR 解析出的 `offers`，`selection_override` 用 `offer.name` 生成 `^\s*自\s*光\s*辉\s*铠\s*断\s*壁\s*$`。同一帧点击时行识别读回正确的 `白光辉铠·断壁`（0.9604），`Detected` 分支读成 `自光辉铠` + `·断壁` 碎块，两个分支都不命中，`LabyRelicSelectOnce` 2.5 秒超时，原生证据为 `recognition_failed=true`、`action_started=false`，没有点击也没有误操作。这是 2026-11-07 静态图标改动的遗留边界：图标身份只进入账本与结果校验，fresh guard 仍停在 name-only 文本身份。

修复（奖励页身份收束）：`RelicOffer` 增加 `identity` 字段，唯一身份入口是 `identify_relic_offers(offers, detail)`（内部 `relic_offer_facts` 解析可信图鉴事实、`attach_offer_identity` 挂到对应 offer）：id/规范名/mark/bonus/rarity 随 offer 一起流动，`_catalog_fact` 在入口统一校验名称与属性冲突。`MaaRelicReward._identify_offers` 在读卡后只调用一次该入口；账本字段、`selection_override` 的 fresh 名称守卫、`_result_name` 与结果复核全部读取同一个 `offer.identity`，不再由调用方另传 `name`。图标不可信或包缺失时 `identity=None`，保持原 OCR 文本路径；属性行、`还剩N次`、按钮、Bright、`LabyRelicSelectedIconCapture` 与点击坐标均不变。没有给 `白/自` 或其他错字增加字符容错，避免放过真正选错卡的情况。

验证（离线）：同一失败帧上，按 OCR 拼写 `自光辉铠·断壁` 构造守卫时 `LabyRelicSelectOnce` 不命中；同一帧挂上图鉴身份 `白光辉铠·断壁` 后命中并产生点击。新增公开测试资源 `tests/data/labyrinth-relic-offer-20261108.png` 与同名 `.json`（保存源、用途、1280×720、sha256；画面不含玩家名、账号、设备或运行标识），测试覆盖：原生 OCR 仍读出 `自光辉铠·断壁`、静态图标仍可信识别 25002/35003/15005、`identify_relic_offers` 把规范名挂到 offer、fresh guard 使用规范名并在该帧真实命中一次点击。另存手动选择后的 `tests/data/labyrinth-relic-obtained-20261108.png` 与同名 `.json`，新增 `tests/test_labyrinth_relic_obtained_frame.py`：从该 ADB 原帧识别 `获得道具 / 获得了以下道具。 / 白光辉铠·断壁×1 / 关闭`，静态图标可信命中 35003，`relic_obtained` 返回 `verified=true`、`identity_method=static_icon`、数量 1。新增测试覆盖：可信身份覆盖 OCR 错读、无事实时回退原文本路径、非法图鉴身份拒绝、`selection_guard` 读取 `offer.identity` 的实际安装，以及 `choose` 只把带身份的 offer 交给守卫且不修改调用方元组。`pytest tests/test_labyrinth_relic_offer_frame.py tests/test_labyrinth_relic_obtained_frame.py tests/test_labyrinth_relic_name_separator.py tests/test_labyrinth_relic_identity.py` 41 passed / 1 skipped；相关遗物、链接、商店与奖励套件 292 passed / 35 skipped；`pytest -k labyrinth` 1873 passed / 127 skipped / 5979 deselected（18 subtests，3:24）。

未做/边界：本局未实机复跑；本次只收束奖励三选一页的身份（offer、账本、fresh guard、结果读取），商店 relic 身份、地图/物品读取和链接/角色奖励的 name-only 守卫仍未并入同一模块；阈值、ROI、超时、图标包与 masterdata 均未改。

## 2026-11-07 遗物身份改为静态图标 + masterdata（原帧校准 + 合成帧离线回放）

同日 `relic_result_mismatch`（见下一条）说明遗物身份仍落在“两帧名称 OCR 逐字一致”上：角色与 Boss 早已是图标/数据身份，遗物是最后一块 name-only identity。本轮把三选一遗物与结果确认改为“静态图标 + masterdata 事实 + 属性行 OCR”的混合身份。

资源：`assets/labyrinth/relic-pack/icons/<id>.png`（91 张 128×128 RGBA，逐字节取自 B服 11.7.2 客户端 bundle `icon_labyrinth_icon_relic_<id>.unity3d`）、`relic-pack/PROVENANCE.json`（与 boss-pack 同构：逐张 sha256/bytes/尺寸/32bppArgb/sourceBundle，游戏图像权利保留）、`assets/labyrinth/labyrinth-relics.json`（master 202611021739 的 91 条 id/name/mark/bonus/rarity/effect/icon；mark 按 `strategies` 词表归一：削弱→弱体、暴击→会心；30001 元素涌动宝珠在 master 无 mark（`mark:null`、`bonus:0`），50005 采用 master 名「拉比林斯之主」而客户端图标文件名为旧名「迷宫大师」；11 张无 master 条目的客户端图标 40006-40010 / 50006-50010 / 999999 不收录）。发布侧 `scripts/build_release.py` 增加 payload 必需项与 `require_relic_payload`（catalog↔PROVENANCE↔源/副本 sha256 三向一致），来源登记在 `docs/LABYRINTH_SOURCES.json`。

代码：

- `src/pcr_maa/labyrinth/relic_recognition.py`：`load_catalog()` 要求 catalog 与 PROVENANCE 的图标清单互为全集，逐张 sha256/字节数/128×128/32bpp 校验后才进入匹配，缺失或漂移抛 `relic_resource_changed`。`RelicCatalog.match(frame, box)` 按相对坐标采样（12×12 颜色粗筛 → 20×20 颜色/亮度/梯度精筛，几何在 art span .60–1.0、偏移 ±.02 上搜索），输出 `RelicFacts` 与 score/rival/margin；`trusted` 需要 score ≥ .62 且 margin ≥ .08，否则 facts=None。
- `relics.RelicStaticIconCapture`：只读 custom recognition，对结果弹窗 `RESULT_ICON_ROI` 与三卡 `ICON_ROIS` 用同一帧匹配；**始终返回 hit**（detail 内 trusted/relic），缺包或异常只降级为 `trusted=false`，因此观察节点不会因身份不可用而 miss。新增节点 `LabyRelicObtainedStaticIdentity` / `LabyRelicOfferedStaticIdentity`。
- `MaaRelicReward` 构造时只加载/校验一次包并注册 custom recognition；`choose` 在选择前先读三卡静态身份：可信槽位与同帧属性行交叉校验，mark/bonus 冲突抛 `relic_identity_conflict`（不猜测、不把错误事实写进 stack 账本）；名称只采用 catalog 规范名，并把 `relic_id` 写入账本字段。结果确认改为“静态图标可信且与已记录的 relic_id 一致”**或**“分隔点容忍的文本身份”二选一（绝不要求两者同时通过）；图标不可信回到文本，缺包时行为与改动前完全一致。`relic_obtained` 新增 `static`/`relic_id` 参数与 `identity_method='static_icon'`。
- 账本 `relic_reward_select` 增加 `relic_id`（有可信图标身份时）；`_trace_detail` 白名单未变，玩家日志仍不打印内部字段。

验证（离线）：本次失败帧 `image-1bc45335…png` 的 `(355,150,95,95)` → 35004，score .7809 / rival .4957 / margin .2852 trusted；同一 run 的另一帧与随机 ROI 全部 untrusted（≤.25）。把该帧记录的回执（标题/说明/道具行/关闭 + 静态图标 detail）直接喂给 `relic_obtained(..., static=True)`：命中 `identity_method=static_icon`、`relic_id=35004`、catalog 名 `幽玄煌装·绝魔`（有/无已记录 relic_id 两条路径都通过；把已记录 id 换成 35003 仍抛 `relic_result_mismatch`）。合成卡面 3 槽 35004/35001/31006 命中，合成结果弹窗图标命中 35004；原生 SavedImages 回放（无设备）两个新节点 `completed=true` 且 detail 正确。Python 侧单帧 match 中位约 12.6 ms、三卡约 37 ms（91 模板，分层几何搜索）。测试：`tests/test_labyrinth_relic_identity.py` 15 项（含 native 回放、校验冲突、回退与缺包降级）、`tests/test_labyrinth_relic_pack.py` 6 项、`tests/test_labyrinth_relic_recognition.py` 19 项（2 项私有原帧缺环境变量时 skip）；`pytest -k labyrinth` 1835 passed / 123 skipped；`python -X utf8 -B scripts/verify.py public` 7285 passed / 398 skipped / 201 deselected（30:48）。

未做/边界：GUI 尚未展示遗物图标（C# loader 未接）；新遗物/新版本必须同步图标包与 masterVersion，缺图标时该卡身份回退文本、不误停；`THIRD_PARTY_NOTICES.md` 已补 relic-pack 行；`docs/LABYRINTH_SOURCES.json` 的 `local_files` 冻结源码哈希在 HEAD 上已有 16 项既存过期（与本轮无关），本轮只更新 `documentation.current_sha256`；未实机复跑本局。

## 2026-11-07 遗物结果校验仍逐字比较，分隔点不一致导致获得后停止（离线复现）

实机失败记录 `run 87473868f7f74c2f9627750693b44117`（业务账本 `954368389b32431baba4d7fdb76dcc14`，`enter_id=13461`，玩家日志 run `8c4cbfcd70b349a687a9b945c57fb762`）：21:24:19 三选一遗物选择 slot 0（`幽玄煌装·绝魔`，守备+3）后，结果弹窗已出现 `获得道具 / 获得了以下道具。 / 幽玄煌装·绝魔×1 / 关闭`，`LabyRelicObtainedObserved` 与 `LabyRelicObtainedExactItem` 两个原生节点均 `hit=true`，随后 Python 复核拒绝该结果，批次以 `relic_result_mismatch` 停止（进程位置 `autoplay.py:792`、`relics.py:612`、`relics.py:500`），进度 3:0。奖励实际已获得，点击落在 slot 0 按钮内，没有误操作。

原因：观察帧把名称行读成 `幽玄煌装`（0.985）+ `1`（0.141）+ `a`（0.327）+ `绝魔`（0.999），显示分隔点被读成独立低分 token 后丢弃，账本记 `relic_name=幽玄煌装绝魔`；结果帧同一行读成单个 `幽玄煌装·绝魔×1`（0.9787）。`relic_obtained` 的 identity 分支里，原生 `LabyRelicObtainedExactItem` 的 `expected` 已是 `_pattern(名称×1)`（分隔点可选）所以命中，但 Python 侧仍用 `_normalized(观测文本) != name+'×1'` 逐字比较，两个拼写必然不等。11-02 同类事故只修了选择守卫的 `_pattern`，结果校验这一处没有同步，"原生命中、Python 拒绝"的两层身份判据一直并列存在。

修复：新增 `relics._item_identity_matches(observed,name)`，用与守卫/原生节点相同的 `_pattern(name+'×1')` 判定（分隔点可选、其它字符与数量仍逐字精确）；`relic_obtained` 的 `icon=True`（identity 为 `LabyRelicObtainedExactItem` 时）与 `icon=False` 两处逐字比较都改为调用它，`_normalized`、阈值、ROI、图标基准回退、未知结果不重发均未改。因为两层现在用同一个判据，原生命中的结果不会再被 Python 以另一套身份定义拒绝。

验证：把这局记录到的原生回执（标题/说明/道具行/关闭/identity，含真实框与分数）与账本名称 `幽玄煌装绝魔` 组成 receipt，直接调用 `relic_obtained`：修复前 `icon=True` 与 `icon=False` 两条路径都稳定抛 `relic_result_mismatch`，修复后两条都 `verified=True`（`identity_method=exact_ocr`）。新增 `tests/test_labyrinth_relic_name_separator.py` 用例：两种拼写接受，替字 `破魔`、数量 `×2`、缺字 `幽玄煌装·绝×1`、尾随字符仍拒绝；`pytest tests/test_labyrinth_relic_name_separator.py tests/test_labyrinth_reward_shop_upstream.py tests/test_labyrinth_links.py` 248 passed / 24 skipped。未实机复跑本局。

## 2026-11-06 主页图标兜底落地（替代文字模板占位，现场截图 + 离线回放验证）

现场实测补充（失败 run `40aa3ff2…` / 运行时 `2f4087bb…` 的导出帧 + 模拟器连续抓帧）：`HomeScheduleText` 的误读不是“永远错”，而是随主页背景/动画在「日程表 / 回程表」之间摇摆——亮色天空皮肤 13 帧中 6 帧读对（0.777–0.978）、7 帧读错（0.777–0.851）；宫子暗色皮肤 6/6 读成回程表（0.854–0.883）。分数都压在 threshold 0.8 附近，因此调阈值/ROI/换文字模板都不够稳。改为用主页右下角**图标行**做像素兜底：图标本身叠在白色圆盘上，两套皮肤上分辨率、位置和像素一致，与页面背景无关。

实现：

- 资源：`assets/resource/image/anchors/home/icons/{schedule,gift,shop,mission}.png`，来自维护者从已安装 CN 客户端 via adb 拉取的原始 UI 图标（`日程表_icon_schedulebutton.png`、`礼物_common_mypage_icon_present.png`、`商店_common_mypage_icon_shop.png`、`任务_common_mypage_icon_mission.png`，30 个候选图标中筛选出的 4 个主页专属按钮）。原始 PNG 带 alpha，而 MaaFw 的 `TemplateMatch` 不按 alpha 掩码（实测原图直接匹配仅 0.42–0.58），所以统一把透明区域垫白（`bgr*alpha + 255*(1-alpha)`；游戏图标渲染在白色圆盘上）后再入库，1:1 对应 1280×720。
- 来源与许可：`assets/resource/image/anchors/home/icons/SOURCE_NOTICE.json` 记录原始文件名、sha256、尺寸、变换、本地路径与本地 sha256；游戏图像权利保留，不并入项目 MIT 许可。活动/主题相关的 `美食嘉年华_main_icon_seasonpass.png`（0.9651 可用）与绶带、特效类图标没有进入必须项，避免活动下线或换图标造成误杀。
- 代码：`src/pcr_maa/anchors.py` 新增 `Icon`、`Page.icons`、`icon_template_path()`、`icon_pixel_nodes()` 与 `HomeIconPixelsLayout = And[HomeIconSchedulePixels, HomeIconGiftPixels, HomeIconShopPixels, HomeIconMissionPixels]`（threshold 0.85、pad 15 搜索窗）；`HomeAnchorLayout = Or[HomeTextLayout, HomeIconPixelsLayout]`，OCR 分支仍在首位。删除上一版 5 个 0 字节文字模板占位与对应 `HomeDefaultPixelsLayout`/`HomeEdgePixelsLayout`；文字 OCR 布局保留为第一判据。
- 输入点击：图标兜底只用于“证明是主页”，`LabyClientHomeToAdventure` 的 `target` 从 `True` 改为固定 `[693,685,53,33]`（冒险词框），否则图标分支命中时会把 And 的点击目标带到图标上。
- ROI：schedule `(989,549,56,49)`、gift `(1188,548,53,53)`、shop `(893,549,54,53)`、mission `(1090,550,52,52)`。

离线验证：两套主页皮肤 + 失败帧上 4 枚图标均命中（0.9156 / 0.9316 / 0.9521 / 0.9382，两皮肤同分），迷宫大厅同 ROI 全部 miss。端到端回放：5 张主页帧 + 3 张失败帧的 `HomeIconPixelsLayout` / `HomeAnchorLayout` / `LabyClientGameHomeObserved` / `LabyClientHomeToAdventure` 全通过，点击落在冒险词框内（如 `[697,710]`）；迷宫大厅帧全部判负且零点击；`LabyGameSessionAfterTitleObserved` 在 8s 预算内主页通过、大厅失败。相关单测 76 passed / 79 skipped（跳过项均为缺私有夹具），`tests/test_home_guard_safety.py` 覆盖图标布局、模板非空、无 alpha、尺寸与词框包含。

现场三主题复测（每主题 20/13/20 帧）：宫子暗色 / 亮色旧 / 亮色当前三个主题上，文字 OCR 首页布局成功率 **0% / 38% / 50%**（`日程表` 仍随背景在“日程表/回程表”之间摇摆），图标布局 **100% / 100% / 100%**；守卫成功率均 100%，耗时约 103 ms（OCR 分支一次尝试 ~51 ms + 图标分支 ~51 ms）。纯推理耗时：OCR 约 8–9 ms/词、图标 <1 ms/个（节点级 ~51 ms 主要是 MaaFw 任务/SDK 固定开销）。

**阈值余量提示（当前保持 `ICON_THRESHOLD=0.85` 不调整）**：三主题实测每枚图标 score 范围——schedule 0.914–0.927、gift **0.859**–0.950、shop 0.896–0.965、mission 0.876–0.942；gift 最低 0.859，距阈值只有约 9 分，是本方案最薄的一处。迷宫大厅负例 0.14–0.26，与阈值仍有一个数量级的差距。若未来主题/活动把某枚图标压到 0.85 以下，优先给该主题追加模板变体（`template` 支持列表，可在 `anchors.py` 的 `Icon` 里扩展路径列表），其次才考虑把阈值降到 0.82；任何调整都必须在三主题 + 大厅负例上重跑，不能只改阈值不验证。

未验证边界：标题页/公告页/副本页/角色页等更多负例、1080p 分辨率（图标位置/尺寸会变）、节日主题皮肤与活动图标替换；上线前仍按 `docs/OCR_STABILITY.md` 补门槛，并把现场抓帧脚本与证据留在本机 `.live-analysis/`（不进发行包）。

## 2026-11-06 主页锚点公共模块，“日程表”被稳定读成“回程表”后由图片兜底（占位验证）

实机失败记录 `run 40aa3ff2f9f7472b97ac7e9fd5674182`（另两次 `f35556797ef044d5a7c985ceb9e872b4`、`cecc59d10cf04c808978b8f9942256cf` 同因）：12:05:27 关闭“通知”并确认为 `client_notice_close` 后，`_after_title` 连续 75 秒探测主页全部未命中，最后一次探测以 `recognition_failed=true` 结束，`_root` 拒绝该回执，批次以 `game_session_readback_unknown` 停止（位置 `game_session.py:556/282/190`，对应 `_close_startup_popup` → `_after_title` → `_root`）。

原因：屏幕确实是主页，`HomeAdventureText`（冒险 0.986）与 `HomeGiftText`（礼物 0.997）每帧命中，但 `HomeScheduleText`（ROI `[986,594,68,34]`、`^日程表$`、0.8）被 v4 识别器读成 `回程表`（0.82–0.90）；扩大 ROI、`only_rec:false`、锐化、1.5 倍放大、JPEG 压缩均不改结果。三次运行合计 217 帧读成“回程表”、2 帧“固程表”、0 帧“日程表”。同一帧整条底栏的其它词（商店 0.999、任务 0.947、礼物 0.999、主菜单 0.989、公会之家 0.997）都正常，所以不是 ROI、阈值、分辨率或加载中的问题；用仓库同一份 pipeline 与模型回放导出原帧即可稳定复现。共享 `assets/resource/pipeline/home_text.json` 的 sha256 与仓库一致。

修复：新增公共模块 `src/pcr_maa/anchors.py` 作为**唯一份锚点定义**（词、紧裁剪词框、所属布局、模板路径），输出三种节点：OCR 叶子与布局（必须与共享 `home_text.json` 逐字段相等，由 `tests/test_home_guard_safety.py::test_shared_anchor_module_owns_the_daily_document` 断言）、`TemplateMatch` 叶子（同词框 + 十余像素搜索窗、阈值 0.9）、以及 `HomeAnchorLayout = Or[HomeTextLayout, HomeDefaultPixelsLayout, HomeEdgePixelsLayout]`。模板资源按页面分目录：`assets/resource/image/anchors/home/{adventure,schedule,gift,edge_schedule,edge_gift}.png`。每套布局只认自己那三个词，OCR 分支永远在前，不做 OCR 与图片的跨布局或跨词拼接。

接入方式（调用方各自决定信任哪个守卫，模块不改任何既有 document）：黎明界在 `game_session_pipeline()` 内 `nodes.update(anchors.nodes())` 并令 `LabyClientGameHomeObserved` / `LabyClientHomeToAdventure` / `LabyClientAfterTitleHomeObserved` 使用 `HomeAnchorLayout`；日常若要启用，只需在 Owner 侧 `anchors.install(resource)` 加 `resource.override_pipeline(anchors.guard_override('EnterAdventure'))` 两行，`home_text.json` 与 `presets.json` 一个字都不用改（`ObserveHome` 仍引用 `EnterAdventure`）。日常默认**不启用**：`docs/SOURCE_AND_LICENSES.md` 规定日常只用有页面约束的文字、颜色和形状识别，游戏图像仅随黎明界集成按 `docs/LABYRINTH_SOURCES.json`、`licenses/labyrinth/` 与资源包 NOTICE 记录许可；`tests/test_pixel_free_resources.py` 把这条落成断言（日常 pipeline 不得声明模板，`image/` 命名空间限定），`scripts/source_delivery.py` 还把旧的主页模板（`home_schedule.png` 等）列为禁止再入公开源码。要日常也启用，需一并处理这三处政策与授权登记。

模板只取文字字形，不取按钮图标或含白底的整块：同一失败帧实测文字紧裁跨帧（另一次运行的主页帧，动画与对话不同）0.951，含白底边框的 84×46 降到 0.866，而按钮整图会被“开放/新内容”角标与选中高亮破坏。负例（黎明界迷宫页、遮住日程表）在 0.8/0.9 阈值下均无命中，±2px 整体平移仍为 1.0。

**当前落地状态：五个模板文件是 0 字节占位**，兜底分支尚未生效，行为与修改前完全一致（不可读的模板只产生 `TemplateResMgr` 加载错误并判定未命中，资源加载、任务与既有守卫都不受影响）。真机取样后直接替换同名 PNG 即可：`adventure.png` `(693,685,746,718)`、`schedule.png` `(986,594,1054,628)`、`gift.png` `(1188,596,1239,626)`、`edge_schedule.png` `(1028,598,1090,622)`、`edge_gift.png` `(1230,596,1280,626)`，全部取 1280×720 原帧、逐字紧裁、同一画面、不含白底/角标/选中高亮；另需补迷宫页、副本页、标题页、遮住底栏四类负例与至少两帧跨帧正例，按 `docs/OCR_STABILITY.md` 门槛记录后再签发。图片属游戏画面，替换时需按 `assets/resource/image/labyrinth/SOURCE_NOTICE.json` 的方式登记来源与权利，不把游戏图像授予项目许可证。

验证：`tests/test_home_guard_safety.py` 覆盖共享定义一致性、同布局三词、词框包含关系、占位状态与 `guard_override` 形态；`tests/test_labyrinth_home_ocr.py` 的入口守卫名同步为 `HomeAnchorLayout`；`tests/test_pixel_free_resources.py` 增加 `anchors` 命名空间与“无孤立模板”检查（日常 pipeline 仍不得声明模板）。相关用例 23 项通过、77 项按私有夹具跳过；黎明界/公会战/引导相关 452 项通过、2 项因缺少设备 shell 原有失败、2 项跳过。离线回放同一原帧确认占位时 `LabyClientGameHomeObserved` 仍失败（行为不变）；临时投放真实裁剪后，同一帧与另一帧的日常 `ObserveHome`、黎明界 `LabyClientGameHomeObserved` 均通过，迷宫页两者都拒绝，验证后已还原为 0 字节占位。未实机复跑本局。

## 2026-11-02 遗物名称分隔点识别不一致导致选择停止（离线复现）

实机失败记录 `run f161b1717150451ab5fc31f02c4f505f`（业务账本 `59a631199d944da7870e4fb9ac06bf84`，`enter_id=13439`，第三次尝试）：三选一遗物页选择 slot 0 时，`LabyRelicSelectOnce` 的 `And` 在 `LabyRelicGuardName00` 未命中，点击未开始，账本把 `relic_reward_select` 结为 `input_execution=not_sent`，批次以 `native_input_not_sent` 停止（进程位置 `relics.py:583`、`relics.py:546`、`native.py:336`）。GUI 的“操作 relic_reward_select 已发出”只是 intent 已落盘，没有发出输入。

原因：观测帧把名称行读成 `白光辉铠` + 低分 `D`(0.24) + `断壁`，显示分隔点被读成独立低分 token 后丢弃，账本记 `relic_name=白光辉铠断壁`；点击帧同一行读成单个 `白光辉铠·断壁`(0.9665)。`selection_override` 用 `_pattern(offer.name)` 生成精确锚定正则 `^\s*白\s*光\s*辉\s*铠\s*断\s*壁\s*$`，`\s*` 不能消费 `·`，所以正确卡片被拒绝。属性行 `【守备+3】（当前：14）`、按钮、Bright 和图标捕获在同一帧均可命中。

修复：`relics._pattern` 把名称内的显示分隔点 `·・‧•` 视为可选而非身份（`NAME_SEPARATORS`）：生成时跳过名称中的分隔点，并在相邻字符之间允许 `\s*[·・‧•]*\s*`；其它字符仍逐字精确，不做替字或模糊放行。正则使用字面字符，因为 Maa 原生引擎不是 Python，不展开 `\uXXXX`。`_normalized`、阈值、ROI、`Detected/Line` 结构与 `only_rec`、图标基准回退、未知结果不重发均未改；`LabyRelicGuardAttribute00`、`LabyRelicBright`、`LabyRelicSelectedIconCapture` 维持原判定。

验证：同次失败原帧 `f7cb533f64fb4fcfb3e38b1425e2dc00-frame.png` 离线回放，修复前 `LabyRelicSelectOnce` 为 `recognition_failed=true`、零点击，修复后同一帧 `action_started=true`，1 次点击落在 slot 0 按钮 ROI 内。新增 `tests/test_labyrinth_relic_name_separator.py`：5 项公开用例（两种观测拼写的守卫都接受、替字仍拒绝、分隔点字面类、其余标点规则不变）与 1 项私有原帧回放；公开用例在旧代码上 4 项失败。未实机复跑本局。同批只读审计还列出角色奖励、角色到达/详情、开局赠送和商店报价确认等处同样要求观测帧与点击帧逐字一致，本轮未改，需另取实机证据后处理。

## 2026-11-02 人工验证码窗口内嵌到主界面

登录触发官方极验时，Python Owner 仍在 127.0.0.1 起一次性页面并用 stdout 发 `kind=captcha`；GUI 不再直接打开系统浏览器，而是在主窗口内容区显示 `PcrCaptchaOverlay` 遮罩层，用 WebView2 原生子窗口把同一页面放进卡片中间。Python 协议、页面 CSP／Host／Origin 校验、`captcha` 消息格式、600 秒人工等待和 810 秒读数预算均未改；只在“显示位置”和取消路径上新增 GUI 侧能力。

生命周期：收到 captcha 消息时显示遮罩并导航；之后任意业务消息、批次结果、停止或异常都会隐藏并关闭原生子窗口；用户点“取消本次验证”时，GUI 对同一本地页的 `<nonce>/cancel` 发 `Origin` 和 `Content-Type: application/json` 精确匹配的 POST，Python 以 `captcha_cancelled` 结束等待，不再需要等 300 秒超时；页面内原有的“取消本次登录”按钮保持可用。“在浏览器中打开”保留为手动回退。WebView2 原生窗口是矩形且总在 Avalonia 内容之上，所以遮罩、卡片边框、标题和底部操作都由 Avalonia 绘制在矩形外侧，原生窗口只填中间占位区，并按主窗口缩放和 DPI 重新同步 `Bounds`。

回退与安全：非 Windows、缺少 WebView2 Evergreen 运行时或窗口创建失败时，记录不含页面 URL 的警告并回退原默认浏览器路径；页面 URL 不进入日志、错误文本或诊断导出。验证页只允许该次 URL 作为顶层导航，禁用 DevTools、右键菜单和新窗口，浏览器 profile 使用进程级临时目录，创建时清理已退出进程遗留的目录。新增固定依赖 `Microsoft.Web.WebView2 1.0.4314.35`，原始许可和 NOTICE 在 `docs/licenses/nuget/Microsoft.Web.WebView2/`；Evergreen 运行时由系统安装，不随包分发。

验证：`PcrProfileSwitch.Tests` 新增 `CaptchaOverlayChecks`（默认隐藏、宿主占位存在、取消／浏览器按钮可访问名、未挂载遮罩时返回浏览器回退）；`scripts/verify.py gui` 通过。真实 MAA 主窗口内已用同一 `captcha.py` 本地页确认渲染、缩放后跟随、按钮触发的 `/cancel` 回传到 Python 得到 `captcha_cancelled`；真实账号触发官方滑块、非 100% DPI 与多显示器移动仍需实机复验。

## 2026-11-01 登录后“活动结束预告”弹窗导致开局停止（离线验证）

实机失败记录 `run 7ee98d5d7adf4ff0ae4787ad0f495b41`（账本在 `.runtime/labyrinth-work/`）：20:05:26 关闭“通知”公告后，客户端在主页停留约 0.8 秒再弹出“活动结束预告”，盖住底部“冒险”。`LabyClientHomeToAdventure` 的 3 秒识别窗口内只读到 `师 0.17`，原生证据为 `recognition_failed=true`、`action_started=false`，账本把 `client_open_adventure` 结为 `input_execution=not_sent`，批次以 `native_input_not_sent` 停止。没有误点、没有发出点击；GUI 日志里“操作 client_open_adventure 已发出，等待确认”只是 intent 已落盘，随后被上述未发出证据覆盖。

原因是启动流程只认识“通知”面板：`notice_pipeline()` 的四个锚点不包含该弹窗，`LabyGameSessionAfterTitleObserved` 不报告这一页，主页大面板白名单 `HomeModalTitle` 也没有该标题，所以它既不能被关闭，也不能作为已知遮挡被拒绝。

现在按真实 1280x720 原帧（标题文字 556,43,166,28；前往活动 732,624,108,28；关闭 465,623,57,30）新增 `home_announcement_pipeline()`：标题 `^活动结束预告$`、`^前往活动$`、`^关闭$` 三个 OCR 锚点同时命中才算该页；关闭动作的 box 取“关闭”文字自身，永远不点“前往活动”。`LabyGameSessionAfterTitleObserved` 增加该页上报，`_after_title` 仍要求连续两帧一致。启动时先关闭“通知”再关闭该弹窗，每个关闭各记一条独立 intent（`client_notice_close` / `client_announcement_close`，后者同时加入 `_OPERATIONS` 与 `restart._CLIENT_NAVIGATION`），每次关闭后重新做两帧稳定读回，最多关闭两次；期间出现未采样页面仍以 `game_session_readback_unknown` 停止。若弹窗在“冒险”点击窗口内才出现，只有原生证据明确 `input_execution=not_sent` 时才允许一次只读观察、关闭已采样弹窗并重试同一次点击；已发出或结果未知的操作不重试。共享主页守卫的 `HomeModalTitle` 白名单加入该标题，弹窗在场时不放行主页。

验证：`tests/test_labyrinth_home_announcement.py`（8 项）覆盖节点/ROI/锚点顺序、操作名与重开集合、关闭与重试的账本顺序、两次上限，以及私有失败原帧回放（启动等待两帧稳定返回 `announcement`、关闭点击落在“关闭”按钮、主页观察与点击均拒绝、无其它输入）。受影响既有用例 311 项通过、48 项按私有夹具跳过；全量 5264 项通过、556 项跳过、54 个子测试通过，无失败。仅离线验证，未实机复跑本局；只按采样到的这一个标题处理，不推广到其它弹窗。

## 2026-10-17 黎明界实时日志面板（仅展示）

GUI 黎明界工作区在“开始任务”栏下方新增“实时日志”卡片，默认折叠（只显示标题行，点“实时日志”开关展开面板；收起时日志仍照常写入日志文件）、最多保留 500 行、只显示黎明界运行行；日常档案沿用原有“运行日志”折叠卡，不受影响。每行是一个 `SelectableTextBlock`，可拖动选中并 Ctrl+C 复制单行；手动上滚后自动跟随会停用，点“滚到底部”可回到最新一行并重新开启跟随。内容来自 Owner 新增的 `kind=log` stdout 行：开局／撤退决策、操作发出、操作确认（含页面与白名单标量）、停止原因（含文件:行）、批次结果，加上既有 `progress` 阶段变化；记录从点击开始一直到结束，所以本地校验、组件、资源、连接、登录和交接阶段的失败同样可见。

`log` 是纯展示通道：投递失败在 Python 侧吞掉，GUI 侧写入失败也不回传，二者都不改变业务结果或账本终态。同一操作只写“已发出”和“已确认”两行，确认行合并了原本的页面观察明细，不再单独出一行确认；完全相同且相邻的两行只保留一行。白名单只放页面名与 `role_id`/`enter_id`/`guild_id`/`difficulty`/`area`/`item_id`/`price`/`count`/`currency`/`outcome`/`attempt`/`current_block_id`/`game_current_block_id`/`game_area`/`fresh_start` 等标量，异常文本、凭据、玩家 ID、URL 和验证码内容永不进入；非 `BatchStopped` 的统一记为 `adapter_failed`。每行同步带 `[黎明界]` 前缀写入 GUI 日志文件（`logs/log-YYYYMMDD.log`），不依赖 UI 线程，UI 忙或挂住时仍有记录；面板“清空”只清面板，不动日志文件。

导出以前两处都按安装目录算，`黎明界独立记录` 恒为 0.0 MiB，且只勾选“黎明界运行日志”导出会报“未找到符合条件的日志文件”。现在普通日志仍取安装目录，黎明界账本、检查点、`events.jsonl` 与原生 `maafw.log` 取业务目录（开发模式为 `pcr-repository.txt` 指向的仓库，打包模式就是安装目录），面板里的逐步日志随 GUI 日志文件一并导出。

离线验证：`tests/test_labyrinth_owner_log.py` 覆盖投递失败不改批次结果、长度与换行归一、白名单不包含异常文本；`PcrProfileSwitch.Tests` 的导出检查同步适配两个目录。GUI 面板的排版、滚动与导出体积仍需实机确认；本轮未改阈值、等待、判定、账本、配置或输入路径，原有未知操作不重发保护保持。

## 2026-10-15 主页偏移布局与半透明公告（仅离线验证）

继续使用 v4。主页入口要求同帧、同一套已采样布局上的“冒险”“日程表”“礼物”完整匹配，保持 `only_rec` 和 0.8 阈值，不混合两套布局。固定位置的“更新信息”或“活动日历”任一命中即拒绝主页；保留原大面板、数据连接和 EXP 就绪检查。观察和点击使用相同守卫，点击目标、次数和等待预算不变。

修复后的真实原图主页入口通过 40/40，负例误通过 0/232；人工变体通过 104/112、负例误通过 0/152，新增部分公告及混合布局负例误通过 0/16。三张人工负例按实际布局修正，修复前后使用相同输入；全部原图、旧样本及原测量保留。两张模糊或平移变体仍安全拒绝，不代表整局或实机验收。详见 `docs/OCR_STABILITY.md`。APP-VER、区域末尾奖励衔接和原账号/地图/结算/Stop/未知不重发保护保持。

## 2026-10-15 APP-VER 读取已安装游戏版本（R54 离线候选）

按固定上游 `AndroidGameProtocolProfileFactory` 的取值方式，黎明界 Owner 启动时读取所选设备已安装的 B 服游戏 `com.bilibili.priconne` 的 `versionName`，用作首次协议请求的 `APP-VER`。复用该 Owner 已持有的 Maa 控制器，只执行固定的 `dumpsys package com.bilibili.priconne`，不新建 ADB 连接，不启动或安装游戏，不接受配置传入包名或版本号。

包记录必须唯一，版本必须是三个由 ASCII 数字组成的字段。命令失败、超时、缺失、重复或格式异常时，以 `game_version_unavailable` 停止，不分配登录客户端，不回退固定版本。查询期间和查询后检查 Stop；原有维护提示更新及最多两次维护请求保持。`MANIFEST-VER`、`RES-VER` 继续读取维护响应并严格校验。

这里动态获取的是游戏协议版本信息，不是自动同步上游源码、事件/角色目录、图标或 UI 规则。上游参考来自项目固定提交 `b8d8af912269cfea9ad65fd4e284d0d64da8e118` 的 `android/app/src/main/java/com/landosol/toolbox/protocol/bilibili/AndroidGameProtocolProfileFactory.kt`。当前 R53 安装保持；R54 以最终 manifest 和构建收据区分候选，尚未实机验证版本读取。

## 2026-10-15 游戏资源版本动态获取

登录时从服务器维护接口读取 `required_manifest_ver` 和 `res_ver`。前者只接受 12 位 ASCII 数字字符串，后者只接受 1 至 20 位 ASCII 数字字符串。两项都通过校验后，分别写入本次会话的 `MANIFEST-VER` 和 `RES-VER`。普通资源版本更新不再要求手改版本号。缺失、类型错误或格式异常时停止，不进入游戏登录。`protocol-version.json` 仅保留核验参考，不再决定运行版本。

首页的“冒险”“日程表”和“礼物”统一使用已采样的完整文字裁剪，直接识别文字，避免不同复合入口的检测分组改变结果。仍要求三个完整文字、原置信度阈值、同帧主页锚点与已知弹窗检查全部通过。页面读回未知时停止，并显示具体提示；不放行 OCR 错词。

已有 `APP-VER` 更新继续使用有界维护请求；本次不新增数据库下载器。账号匹配、停止、固定请求范围、响应结构与地图验证、账本和未知操作不重发保护保持。角色、事件和图像资料仍独立加载，协议或游戏内容变化可能需要后续维护。

用户已授权离线验证后更新本地 R52 候选。安装和真实运行状态以本轮收据为准，不能从源码修改推断已经安装或实机通过。以下版本和验收记录保留其历史范围。

<!-- R38_REUSED_CLEAR_DELIVERY_20261009 -->

## 2026-10-09 当前交付范围

用户明确选择复用已有通关证据交付，不再补跑随机分支。当前修复后的149难度5完整通关、成员范围/实际三队/战斗奖励、单队三败切多队胜利和最终MAP/动画/宝箱/道具/首页均保留真实证据。自然READY及attempts计数1有效。

同批双成功计数1、2未完成，原不同版本和批次的成功不拼接。空有效获胜、季节变体、零余额离店、free class1/2、选择印记与刷新、1315/1102原规模后继、同页替选、原始既有开局接管及148精确MAP背景后JOINED分支未覆盖，详见RELEASE_STATUS.md。以下旧暂停/草稿及两局要求只作历史，由本节与最新用户决定覆盖。

<!-- R38_CURRENT_DELIVERY_DRAFT_20261009 -->

# 黎明界bot · R38 集成与验收草稿

当前集成基础为 R37 日常活动过期修复，承接 R36 普通导航 OCR 设置；目标交付版本为 R38。固定上游提交 b8d8af912269cfea9ad65fd4e284d0d64da8e118 与原许可、组件来源登记保持。目录名 candidate-r28-source 是历史目录名称。

协议读取实际账号绑定、局身份和路线，Maa 负责真实页面、角色、队伍、战斗与奖励操作。SDK 票据仅当前登录窗口内存持有，人工验证码仍由玩家处理。新 SDK 已按真实维护元数据核对协议资源版本 202609281226；不绕过版本不匹配守卫。

目前仍在开发实机覆盖，正式冻结同 SDK 同批难度5双局为 0/2。本草稿不能替代真实 started/cleared/cleared/finished 与计数1、2，也不表示已安装 R38。保留已有效的成员范围、实际三队 Boss、连续奖励及真实战败耗尽后的生产续局证据；未完成的实机分支继续分别登记。事件角色增益计算仅在当次静态资料副本内复用原计算，评分结果与候选顺序保持，原计时起点、阈值及次数上限保持。

最终匹配运行文件、源码与说明由清单绑定；精确37→38补丁保留全部真实设置与账本，完整包用于新目录。以下旧开发状态和日期记录为历史，不覆盖本节。

# 黎明界bot集成候选

## 当前开发状态（2026-10-06）

沿用固定上游、现有 source 与 candidate-r28-source。正式发布登记为 R33 / GUI 2.16.6，现用安装仍为 R32；本候选集成 R33 日常基础，最终目标为 R34。目录中的 r28 名称为历史名称。尚未完成正式 R34 打包或安装。

第61局完整奖励链已核：观察时为道具关闭页，实际原生动作时已进入角色选择，原关闭点击明确未发送；页面推进的原因尚不能确定。共享 close_reward / settlement_next 路径遇本动作明确未发送时，仅交回现有页面路由读取一次，接受已推进的预期页面。旧动作不重发，原账保留。阈值与等待预算不变。23项专项及36项路线集成检查通过。

第67局在30501已发送选择，但获得页因跨字体OCR“影勿剑幽影/影刃剑幽影”精确名称比较失败，关闭未发送；原账4678及4444前缀保留。共享路径现从当前选择动作同帧学习所选图标，以Maa原生内存TemplateMatch独立核获得图标及×1，不做替字、模糊文字放行或像素相等要求。207个不同受影响检查通过；原67选择帧缺失明确保留，原结果与显式合成捕获回放不冒称旧动作已验证。第68局10601选择/独立结果/关闭实机已过，20402取得青辉之环×1后图标NCC0.816982低于.85导致必经图标回归。现恢复原精确名称×1成功路径，仅名称失配时使用当前同帧图标替代身份证明，独立数量保留；同资源注册回调按既有observer缓存复用。193受影响检查无失败/跳过，包含68原帧、错名缺图拒绝、跨字体、错图标/数量、同正式Context两个新实例和Stop/未知边界；原账不补写成功。第69局准备复用SDK，难度1、目标公会1；私有入口补保存当前选择同帧以便未来诊断，不进入公开包或资格。第64/66局三笔局内币购买、独立结果、关闭与退出回地图已实机通过；第66局Boss新鲜定位及预览输入已通过，国服“首领战格子”文字识别缺口已在共享描述、类型映射及同类挑战页修正。7项原帧/位移/当前控件与中英文标题检查通过，受影响场景、确认和收尾共90项通过。阈值、等待预算不变，不增加资格守卫，不要求像素或跨帧OCR框相等，不重发旧动作。

第70局两笔局内币购买、独立结果、关闭及确认离店已实机验证，见私有SHOP70_ACCEPTANCE回执。第70局进入Boss30701编组后，页脚装饰点被OCR读为“·队伍1”，初始读取失败且未发Boss开战；共享BossMemberLabel仅规范化开头装饰点，ROI、0.8阈值及等待预算未变，30项原生/编组检查通过。第71局10601名称整行OCR/.829905低于原.85，未发送选择；同ROI检测模式/.969775正确，共享观察与实际选择核验保留整行模式并用Maa原生Or补检测模式，副标题只允许终末句号差异。当前候选195项完整遗物检查全过，无失败/跳过。第72局复用SDK创建后多条遗物链已实机通过，30601商店确认因OCR同行顺序使系列属性丢失而停止，确认消费明确未发送；5544行账本终态、原5300前缀保持。共享确认解析按本帧系列效果行读取，原生点击核验和实际购买复用，41商店专项全过，阈值/ROI/等待不变；旧购买不重发。第73局两笔购买及关闭已实机通过，但第三件NEW徽标被识别为INEW拼入真实商品名；只打开确认、未创建第三笔消费意图，5814行原账终态并保留原前缀。共享三槽名称ROI排除右侧徽标，名称/价格/属性核验、0.8阈值和等待不变；47商店专项全过无跳过，六合成槽位/位移原始失败保留。第74局20401后地图定位停止，原EX可见但y640裁切及拒绝峰过早抑制邻域导致漏检。共享完整画面边界及只抑制已通过候选修复，原候选两原生回放复现，当前100项地图/节点/原生预览和导航专项全过；阈值、等待及24候选预算保持，旧滚动不重发。5954行账全部终态且5814前缀保持。第75局10503连接奖励后加入三行角色，霸瞳皇帝头像歧义而名称/.991827正确，原加入读回缺名称备用而停止。共享加入仅对不可信头像复用现有名称ROI/.85和精确目录解析，可信头像及预期身份核验保留；56项不同自动加入/奖励/等待/派发检查通过，结束帧与首行位移回放过，第三行两渠道不可信继续拒绝、不猜名字。6040账终态/5954前缀保持；结束帧不是旧回调帧，原结果不追改。第76局第三层顶部30303遗物完整框y59被y80搜索/绑定边界裁掉，原定位帧证明页面持续地图；共享纵向搜索及所有绑定恢复完整画面y0..720，原阈值/等待/24预算保留。104项地图/原生预览/导航专项通过，原帧及独立位移绑定30303；6232账终态和6040前缀保持。第77局三笔购买与取得关闭通过，离店模板下沿点击未触发关闭；原生回执点[1189,671]及后续商店读回确认，原旧动作不重发。共享SHOP_CLOSE只复用原ROI/.8的关闭文字与原生当前控件输入，34项场景/输入原帧与独立位移检查通过；6496账终态、6232原前缀保持。第78局最新购买/结果关闭/离店确认/移动Boss完整链已实机通过，6802账全终态、6496原前缀保持。Boss第三队读回被底部重复文字误拦；共享Boss Read删除该重复锚及相等门禁，沿用原生三页签、当前选中队伍、实际成员及开战前完整逐队读回。32项受影响检查通过，不改阈值等待、不重发旧动作。第79局Boss三队编辑/逐队复核/开战及WIN通过，结算输入明确未发送；7112账全终态、6802前缀保持。原生UNKNOWN临时Boss别名随后成为正式boss_summary，共享零输入收尾路由只按实际来源类型比较并交回现有路由，真实同页/未知输入保持停止。90项不同收尾及完整状态机专项通过，原生完成事件与旧测试桩问题只修夹具。第80局三购买及结果关闭完成，离店及确认输入成功，首次读回确认标题淡出后的商店背景被旧等待误拒，随后新截图为地图。共享确认后等待只接纳原商店背景为来源，保留map目的和原30秒预算，不加点击、资格、阈值或重试；31项受影响专项通过。7376账全终态、7112前缀保持。第81局20401钢铁坚铠2100购买提案后停止，未提交购买；原生确认正文夹入后景6钢铁坚铠，旧拼接误得21006。共用确认/取得核价保留词块分隔，兼容金额分词、不吞后景数字；53项受影响专项通过，7516账全终态、7376前缀保持。第82局Boss及最终真实CLEAR已观察，关闭前因重复分数明细OCR缺失误分普通结果而明确未发送；尚未主页和协议计数。只移除共享CLEAR规则的重复明细条件，保留结果Logo/CLEAR标记/关闭三模板；91项受影响专项通过，7856账全终态、7516前缀保持。第83局Boss/真实CLEAR及关闭输入已确认，关闭后自然地图背景误被收尾等待拒绝，结束帧宝箱动画后自然完成；已观察CLEAR后仅交回原30秒宝箱/最终道具/主页路由，101项受影响专项通过，8186账全终态、7856前缀保持。第84局20401滚动后左侧20502 EX整列被x180裁切排除，已仅修共享完整屏幕搜索，107项地图回归通过、低置信动画相位仍拒绝，8330账全终态和8186前缀保持。第85局20301已选1293在成员边框裁切时漏识别，已仅补回现有固定成员框并保留全部竞争，61项角色回归通过，原阈值/微调半径/12秒预算不变。8454账全终态和8330前缀保持。第86局协议创建后下载重试ACK成功，残留弹窗使启动读回过早结束；已仅将残留页交回原75秒预算内只读推进，72项不同受影响检查通过，8464账全终态及8454前缀保持。候选空闲时承接R35四项日常修正，私有source保持。第87局邀请已ACK并结算，但开局角色加入标题读回误用奖励ROI而拒绝；共享解析器仅按已有开局标题/关闭ROI校正，84项不同同类检查通过，原阈值和等待不变，8488账全终态及8464前缀保持，R35四文件不变。第88局完整单局验收通过：开局/路线/奖励/三笔购买/多队Boss/最终CLEAR/黎明界主页/协议top0及批次计数1，8836账全终态和8488前缀保持。私有单局入口按设计退出；最终冻结同批两局尚未开始，先做真实刷取和上限分支。GUI共享Owner补齐Stop回收完成后的最终停止提示，42项离线检查通过；当前Core已实际重开载入美食殿堂5次、玩家ID为空，保存重开已核；当前Core的GUI Stop后编辑已按用户“已停止并保存5”人工完成回报收口，保存与实际重开5已独立核实；最终停止文字42离线通过，工具未独立取得新停止标记或原生最终文字的限制保留。

真实完整通关仍为0/2。剩余为单局Boss→最终CLEAR→主页及协议计数、真实刷取与次数/时间上限。GUI Stop后编辑与保存重开已按上述人工及独立保存证据完成。必要修正与分支验收结束后冻结功能输入，在同一批次连续真实通关两局；功能代码再变则重新完成最终两局。最后核匹配源码、全新解压、精确R32升级与全部私态保留后安装。以下日期记录为历史，不覆盖本节。

## 历史方向（2026-10-04 用户修订）

当前暂停开发和实机测试。双配置及主要状态机已接入，真实完整通关仍为0/2；尚无合格最终运行包、匹配源码、保留数据升级补丁或现用目录安装。本节覆盖历次尚未实施的修法，原始失败证据保持。

当前正式版本及本地安装为R30，候选目录名candidate-r28-source为历史名称，其日常部分已窄同步R30；功能源source从冻结r24开始。恢复后继续现有候选，不重新移植，不混培养/采购实验；交付基线以届时实际manifest为准，默认R30升级。

### 修正过度防御

第21局已成功邀请初始三人并打开普通移动确认，确认Click却未发出：原OCR字框宽60，新鲜框宽59，当前scene要求跨帧字框完全相等而失败。该限制重复了MAA PCR此前已纠正的逐像素/画面一致性检查。

恢复时优先复用已有Maa原生OCR、限定ROI、页面锚点、Pipeline输入和新鲜业务读回，取消跨帧字框严格相等；不改成硬编码59、不堆容差/稳定帧，不为此按钮新增CustomRecognition精确中心1×1输入方案。已实机有效的上游角色和节点点位算法保留。

账号/设备、正确节点和角色、价格/余额、明确战败/停止，以及输入结果未知禁止重复操作仍保持。完整识别不等于业务成功，像素摘要用于来源记录而非正常执行资格。转场由Maa有界等待与重读处理。

### 已实现

两个固定配置分别保存设置和设备：旧pcr-alpha-v1显示“日常&扫荡”，pcr-labyrinth-v1显示“黎明界bot”。共用执行互斥，运行时禁切配置/设备，停止完成后恢复编辑；GUI Headless与相关日常边界有通过证据。

执行链OwnedRuntime→ProtocolAcquisitionPort→MaaGamePort→AutoplayRunner已接通。国服B服独立top/enter/resume/retire协议读取完整地图和创建/刷取，Python计算路线及策略，Maa负责实际邀请、移动、战斗和购买。原只读同步客户端保持只读。模板/价值/容差及第三区/第五区Boss条件已移植；角色、普通/EX/Boss编组、遗物、事件和商店策略使用固定上游数据。

地图、确认、挑战、战斗、胜败、角色/连结/遗物、事件、商店、休息、区域、宝箱、结算和最终CLEAR已有分派。完整地图实读44节点/3区域；局部画面不冒称完整路线。最终Boss胜利、明确最终CLEAR与新鲜主页核验共同计通关，普通WIN/RESULT/路线结束不计。默认目标1、难度1、刷取关闭；每局100次或30分钟上限，允许局内货币购买、不刷新商店。不接半途局、不自动恢复崩溃现场。

凭据仅本机窗口/标准输入/当前会话内存，验证码人工；不保存密码/SDK会话到配置、命令行、日志或文档。游戏阶段暂停协议请求，下一局重新核同一账号。独立意图和运行账本保留结果未知状态；未知输入不重发。计时覆盖截图、识别、策略、输入和页面等待，不承诺提速比例。

### 已验证与剩余

初始三人邀请已多局实机成功；第19局到首个普通战斗编组页，第21局到确认弹窗。角色精确点修复的11项离线检查及最新实机邀请通过，节点32%点已实机命中。路线/策略、场景、购买限制、失败停止和官方CustomController有定点离线证据；既有整套原失败与后续通过分别保留，不改称同轮全绿。

当前候选尚未完成单场普通战斗到奖励、更未完整通关或连续两局。随机事件、完整购买/Boss/区域/刷取交接和最终GUI整链需要实际覆盖。恢复顺序：修当前非业务OCR限制→一局完整通关→两局循环→补未覆盖验收→最终冻结/匹配源码/保留数据补丁/安装。工程当前计划与交接分别在docs/LABYRINTH_PLAN.md及docs/LABYRINTH_HANDOFF.md；禁止直接照旧continue脚本开局。

## 来源与许可

固定上游为[wbero/limingjie-assistant](https://github.com/wbero/limingjie-assistant)，commit b8d8af912269cfea9ad65fd4e284d0d64da8e118。组件、资源和实际SHA见LABYRINTH_SOURCES.json。历史原记录LABYRINTH_INTEGRATION_HISTORY.md保持原字节；本次重整前的现行文档另在私有开发证据目录原样归档，不改历史证明。

上游自有代码/策略为CC-BY-NC-SA-4.0；AutoPCR vendor、MFAAvalonia、MaaFramework、运行时及项目原创保留各自许可。801角色图标与场景/节点图像按来源登记，游戏图像权利归各权利人；公开可下载或来源记录不等于获得公共分发许可。本机候选验收和公共发布资格分别处理。


2026-10-04 恢复开发：Scene动作沿用当前原生OCR ROI和And点击，取消跨帧字框相等；当前页面、控件身份和节点类型保持。17项定点离线检查通过（含第21局原失败帧正常宽度波动、错页/错类型、Stop和未知ACK）；不代表实机通关。原21局仅追加确认输入未发送维护事实，旧账前缀及失败报告保留，独立撤退后从新局验收。

2026-10-04 第22局遇登录下载错误/通信超时：专属原生OCR与And重试加入现有标题交接，每次启动最多一次，仍在原75秒预算内；Stop、再次错误和未知ACK结束。16项定点下载/标题回归通过，22局原失败保留；真实通关仍待验收。

2026-10-04 第23局编组发生无意义滚屏：水属性单卡暂未识别，边缘检测错误要求可信角色；已移除此条件，连续两次列表观察未变就结束当前方向搜索，筛选后已找到目标立即返回。角色身份仍由现有识别核验，不靠边缘判定补身份。16项定点回归含实际停止帧原生两次Swipe；真实角色输入中心逻辑保持。原停止末次Swipe仅Starting，输入与结果未知的维护事实已追加，原279行账前缀和分卷日志保留；旧滚动不重发。

2026-10-05 用户再次纠正像素自限：队伍顶部/成员栏均复用现有属性徽章识别；顶部数像素裁切和橙色插画误判选中不禁用可见徽章，开局裁切遮罩仅用于opening。角色绝对置信度和不同角色间隔不降低。稳定性比较页面、角色身份、成员/选择状态，不比较卡框、滚动条、未知纹理；跨帧viewport签名不再否决当前原生Swipe。保留业务身份、成员实际读回、Stop及未知输入不重发。35项定点检查通过，含两张实际失败帧找人零Swipe、一像素几何变化仍稳定及真实点击后底部1059/1075读回；证据character-business-stability-01/test02.log。25局没有找人滚动，Click ACK已知但原成员读回失败，仅追加维护不补业务成功。真实完整通关仍0/2。

2026-10-05 第26局已真实三人选齐并获得普通battle_start outcome=won，零找人滚动；settlement_next Click ACK后读到下一步尚未出现的道具动画而原报scene_unknown。已复用原有30秒ACK转场循环（不重置预算、不重发输入），一般动作也等待当前业务页面，Stop/断线/预算到期/未知ACK保持停止。38项转场/自动路线回归及6项结算专项通过（含实际动画→按钮画面官方SavedImages纯只读复现），不计完整通关。26局原419行前缀保留，仅ACK维护至420行；attempt-16原日志两分卷及失败报告保留。sequence13新27局正在验收。

27局道具Next ACK后旧道具页不当最终读回；现等待角色/地图等业务阶段推进，同页仍在动画只读等待。40项结算/转场/自动路线回归通过；实际27角色奖励页只读原生OCR解析1016/1182/1340及既有选择条件通过，未发实机输入。当前28局继续真实验收，完整通关仍0/2。


2026-10-05 当前实机仍0/2，正式R30。奖励页复用现有CharacterRecognizer头像包与既有阈值，Maa当前帧读取三角色并按上游策略选择；生产选择链用当前角色身份替代九个旧OCR小框，Pipeline原生选择按钮Click及明确加入结果保持。真实29帧含千歌/毗伽菈OCR误读，9项针对性通过；共享角色/Autoplay回归28项通过，旧计时夹具根/动作名不一致单项修正后通过。30局真实选择1339并确认加入，未完整通关。
30局首张后战斗地图可见10301，却因三行仅识别两行被完整列/可达子集长度要求拒绝，随后滚过目标。保留按序模板识别和目标激活，绑定允许完整列的连续可见行；滚动后的整屏哈希变化不作资格。原30地图及Maa节点读回下4项业务目标检查、4项滚动/Stop检查、20项旧Autoplay/真实地图检查通过（24+4分次，新增夹具构造错误已修正）。原598行主账全部结算、38行独立撤退账及Native末帧保留，真实通关0。证据角色role-reward-current-01，地图map-current-01。GUI隔离副本补包内.NET，实际保存2→3、关闭重开读取3、再编辑4通过，日常配置原字节不变；图像捕获/点击几何工具错误，实际Stop后编辑尚未验收，不能以Headless代替。


2026-10-07 收尾基线更新：普通导航候选R36已由独立会话实际安装；候选仅承接其三份精确Pipeline文件，保留黎明界识别阈值及原等待。用户授权停止100，原账及未发送证明保留。最终版本R37、真实安装精确36→37；当前最终两局重新冻结后等待人工登录，未宣称两局通过。单局与刷取分支证据保持。
