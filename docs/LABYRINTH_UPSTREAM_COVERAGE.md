<!-- R38_REUSED_CLEAR_DELIVERY_20261009 -->

## 2026-10-09 当前交付范围

用户明确选择复用已有通关证据交付，不再补跑随机分支。当前修复后的149难度5完整通关、成员范围/实际三队/战斗奖励、单队三败切多队胜利和最终MAP/动画/宝箱/道具/首页均保留真实证据。自然READY及attempts计数1有效。

同批双成功计数1、2未完成，原不同版本和批次的成功不拼接。空有效获胜、季节变体、零余额离店、free class1/2、选择印记与刷新、1315/1102原规模后继、同页替选、原始既有开局接管及148精确MAP背景后JOINED分支未覆盖，详见RELEASE_STATUS.md。以下旧暂停/草稿及两局要求只作历史，由本节与最新用户决定覆盖。

<!-- R38_CURRENT_COVERAGE_DRAFT_20261009 -->

# R38 上游覆盖与验收草稿

当前采用固定上游 b8d8af912269cfea9ad65fd4e284d0d64da8e118，运行基础 R37，承接 R36 普通导航设置。原19项配置、12条共享调用链与4项外层分支清单继续有效；下方早期暂停状态属于历史。

已有效的实机证据包括成员范围贯穿到实际 Boss 三队、战斗及同一奖励的角色2→1合并后继，以及空有效列表回到 ALL 后三次实际战败耗尽、生产退局续开。空有效的实际获胜奖励后继仍需补齐；非空有效获胜不替代这一项。开发中的启动退局续开也不替代完整未开始开局的接管证明。

基础名与季节变体、零余额离店、自由邀请class1/2、选择印记、同页事件替选、单队转多队、真实自然 READY 恢复，以及最终 MAP/动画→宝箱→道具→首页继续按原清单验收。保存帧、离线检查和开发局分别留证，不替代对应完整实机分支。

正式冻结同 SDK 同批美食殿堂难度5双局当前 0/2，R38 尚未取得最终交付资格。最终交付说明在实际验收后更新；以下既有覆盖表、来源与日期记录保留为历史证据。

<!-- LABYRINTH_SHUTDOWN_20261008 -->

## 2026-10-08 收工交接（当前；覆盖下方历史状态）

用户要求修复完成后收工，准备关机。本轮已完成122空有效列表根因修正和必要回归，**暂停开发/实机，不启动123、不自动续跑、不撤退122、不恢复旧动作**。122已真实finished(character_readback_unknown/count0)，formal620cb8418be54070bc9b8990d9cc92a7/nativeb400772e3b6248f89f2346c2384f3b34，2026-10-07T17:15:29.217624+00:00。原3280条隔离账SHA3f59033d251efa39448ad7530e55de8f9b240395692d7c213cfacef6a884fd18及121前3042条、109原未知均保留。SDK97160 seq20当前child_active=false、内存登录保留；关机后不能承诺内存会话存续，明天先核实际进程/SDK，已失效再重新登录，验证码人工；不保存凭据。

122的EX30501 challenge和有效过滤输入均有完整ACK及native action started/finished；保存帧明确“！未搜索到该角色。”。原OCR区域[525,330,260,150]遗漏战斗提示y274，且正确区域OCR把警示图标读为前置“！”，旧正则拒绝。只修共享character_pipeline.EffectiveEmptyNotice：ROI改[525,250,260,230]覆盖battle/Boss提示，允许单个明确!／！警示符，仍要求完整原句；.8阈值、1000ms节点、原12秒读回、guard、成员范围、100次/1800秒不变。复用现有explicit empty→零滚动→ALL→effective_ids=()→评分/实际队伍链，未凭无卡判空。实际122保存帧原生Read已通过，五名真实成员保留、零点击/滚动。此为原有空分支识别接线缺口；尚无修正后实机完整empty→ALL→战斗证明。

10模块原XML152通过/1旧ROI夹具断言失败保留；更新该断言后两个模块77/77通过，替换对应用例后153项当前唯一有效通过，不宣称原失败XML全过。两个新增检查为122真实原生帧及battle/Boss区域/明确提示与反例；D/S源码、两个公开测试模块、上游来源哈希已同步。证据E/isolation-after109/RUN122_REVIEW.json、SHARED_EMPTY_EFFECT122_REVIEW.json、empty122-final.xml。独立四类修复已按根因合入且保留118资源回调复用/117成员读回；合并531检查、GUI Release及42实际控件编辑保存重开复用；121三个已识别最终动画的确证未发送纯观察恢复一行修正与518当前有效检查仍保留，实机后继未完成。原1879通过/12失败/305跳过XML仍为历史失败结果，WinError145清理控制复现不作为业务缺陷改代码。

原范围保持：19/19参数完成默认/校验/GUI保存重开/快照/决策接线；12/12共享链和4/4外层适用行为已接线并有离线证据，但实际完整分支验收仍有缺口。最终冻结同SDK同批两局0/2，R38最终打包/精确37→38保留数据升级/全新解压和桌面两个最终ZIP均未完成。正式发布R35、实际安装R37，不能宣称已发布R38。详尽进度与明天可复制提示词见docs/LABYRINTH_HANDOFF.md本次收工节；唯一覆盖清单仍为docs/LABYRINTH_UPSTREAM_COVERAGE.md。

### 完成度口径与剩余实机项目

| 范围 | 已完成 | 未完成 |
| --- | --- | --- |
| 19策略参数 | 19/19默认/校验/GUI编辑保存重开/不可变运行快照及真实调用接线；原10项及真实设置保留，GUI42实际控件和旧44/配置48有效证据 | 不把离线参数贯通声明为每种参数组合都已实机验证 |
| 12共享链 | 12/12适用上游行为接线及回放；开局、普通/EX组队胜利、多批普通奖励、连结/遗物、300刷新续购、商店离店确认、多队Boss胜利均有具体局部实机证据 | 按下表分支继续验，不能以一次局部成功升级整类 |
| 4外层差异 | 4/4已有实现和回放，任意半途恢复不属于固定上游能力；safe ready约束/替代真实撤退路径已登记 | 未推进开局复用、生产启动处理既有局、attempts真实终态计数、ready恢复仍无当前生产完整实机证明；私有验收退局不替代此证明 |
| 独立检查 | 4/4按片段合入；531合并受影响检查；GUI Release编译通过，42实际控件编辑保存重开验证 | 最终恢复和自由列表/完整邀请等关键实机后继仍缺；其他原失败XML不改为通过 |
| 当前两项修复 | 121最终动画纯观察518当前有效检查；122空有效列表153当前有效检查/实际保存帧原生零输入 | 两项修正后完整实机后继待验 |
| 最终验收/交付 | 发布/实际安装/补丁基线已核，交付工具已有 | 最终同一SDK同批真实两局0/2；R38匹配运行/源码/说明、37→38精确保留数据补丁、新解压和升级安装、桌面两ZIP未生成/验收 |

| 共享链 | 当前实机命中 | 真实剩余 |
| --- | --- | --- |
| 1 自由全列表/职阶邀请1/2 | 开局三人邀请完整读回已通过 | 11072/33151/33152和商店选择印记的自由1/2及完整加入后继 |
| 2 连续多轮三选一/连续加入 | 普通角色奖励及后继多次命中 | 同一完整多轮批次与连续加入结果 |
| 3 动画推进 | 120–122所属召唤/奖励动画推进；121到真实Boss胜利/最终分数关闭/宝箱动画 | 121最新纯观察恢复→实际chest→item→home→真实协议终态；本批最终两局 |
| 4 事件拒绝替选 | 122事件1111选择11112完成普通事件后继 | 游戏实际拒绝/已授权同页6秒超时后的不同替项后继 |
| 5 随机/选择印记 | 119随机900真实购买/完成关闭 | 119修正后的actual joined→货架→地图；选择印记自由邀请链 |
| 6 商店刷新 | 119/120真实300报价、扣费、新货架续购；121退出确认→地图通过 | 印记后续整条商店链仍依赖第5项；真实保存刷新false不覆盖 |
| 7 14EX/冰狼 | 不同EX详情/有效角色/组队/胜利及Boss多队实际命中 | 其余策略所需详情、目标数、机制偏好按命中登记，不把14配置当14实机 |
| 8 有效列表 | 非空EX多次扫描→ALL→组队/胜利；122空列表已复现 | 本次空列表→ALL→空评分→实际编队/获胜；Boss空分支 |
| 9 失败重试排除 | 离线失败队排除/有界重试通过 | 实际战败后重试及排除阵容 |
| 10 单队Boss转多队 | 普通多队Boss配置/开战/胜利通过 | 单队达配置条件后的实际切换 |
| 11 失败/撤退/继续 | 多次私有失败样本独立真实撤退，新局创建隔离 | 生产retry耗尽failure_verified→真实失败记录→retire→批次继续；不把unknown失败样本当verified defeat |
| 12 遗物层数/主追 | 遗物奖励及多次商店实际当前层数决策已命中 | 跨同一奖励/商店完整主追保持按实际业务证据继续登记 |




122已真实启动：formal620cb8418be54070bc9b8990d9cc92a7/nativeb400772e3b6248f89f2346c2384f3b34，2026-10-07T16:54:30.804277+00:00 started；SDK97160 seq20登录保持，launcher169732/原生152256日志核实。新创建f8237956c89b415b856d7be24299f1d5，未观察到121撤退动作；既有私有入口仅在fresh TOP无局时跳过撤退，未补121通关计数或旧动作成功。3042条121前缀及109原未知保持，1061功能资源冻结，真实设置难度1/刷新false/Boss multi无覆盖。本局目标最终动画自然变页纯观察→宝箱→道具→首页完整读回，印记可买则验实际加入；开发单局不替代最后冻结同批两局。依据E/isolation-after109/BRANCH_AFTER121_START_RECEIPT.json。下文为历史状态。

121共享最终动画后继缺口已一行补齐：三个已识别动画复用确证未发送后的纯观察恢复，不扩大UNKNOWN重发/不加点击、不改原3秒/阈值/100次30分钟。4项真实根因反例由失败转通过；6项新增含未知输入/未变页不重发边界，121真实宝箱帧恢复零输入。25模块原XML517通过1新夹具异常类型断言失败保留；修正后模块13/13复核，按同一源码唯一用例覆盖共518当前有效通过，非声称原全批XML全通过。D/S、来源和24公开测试闭包同步，509收集、公开模块12通过1私有帧跳过。GUI42实际控件编辑保存重开及原合并531证据按未变范围沿用。SDK97160 seq19空闲登录保持，121原3042条账与109旧未知不变。下一122已prepared，功能1061中只autoplay.py较121改变，真实快照无覆盖；目标最终动画自然切页纯观察→宝箱→道具→首页，印记可买则继续加入验收。仍是开发单局，最后冻结同批两局/R38交付未开始。证据E/isolation-after109/SHARED_FINAL_ANIMATION121_REVIEW.json、BRANCH_AFTER121_START_RECEIPT.json。

121已于2026-10-07T16:39:35.795054+00:00真实结束native_input_not_sent/count0，SDK97160 seq19空闲内存登录保持。3042条账SHA08169e3328cc110a1986e7f84563c5a199da3d74213c133d30ddc7facde4a150、120原前缀与109未知保持，1061运行输入整局不变。本局实机通过商店三笔遗物→完成关闭→离店确认→地图、EX有效6次→ALL→组队→胜利→奖励及Boss多队配置→开战→胜利/结果页关闭。未买印记，不补加入证明。最终已观察clear并关闭分数页，随后观察获得宝箱动画；输入前自动切到实际宝箱开封结果，task200024769完成/无输入/recognition_failed，不是预算耗尽或未知ACK。原OID64e58dd0db8c4b3d86f5884bd7a8d0cb保留not_sent，不补旧通关计数。共享_do原纯观察恢复漏advance_final_animation，安全空闲后一行纳入；三个最终动画和121实际帧四回归通过、零恢复输入。25模块518项受影响检查运行中，尚未宣称通过；未知输入及未变页边界同时重核。下一实机待检查、镜像来源/公开源码同步完成后再启动，当前不重开/不恢复旧局。19/12/4与最后冻结同批两局/R38交付继续。依据E/isolation-after109/RUN121_REVIEW.json、animation121-fixed.xml。下文运行状态为历史。

121已真实启动并正常推进：formal71a40b405b7349f79eba1803a39142b5/native1737c7893b05448e9a57bc8baa9788c6，2026-10-07T16:02:47.497659+00:00 started；SDK97160 seq19登录保持，launcher167152/原生105368归属与日志核实。120独立真实撤退ac6ebf2d06cc461b811b136751b99da5；121创建4ac66168d42b44199b3762e261c5ebc2。2628条120账前缀及109未知保持。1061功能/资源冻结；使用真实保存快照（刷新false、Boss multi、难度1、100次/1800秒），无私有覆盖。目标商店退出确认→地图、印记加入及Boss/最终结算；仅开发单局停止，不替代最后同批两局。依据E/isolation-after109/BRANCH_AFTER120_START_RECEIPT.json。下文运行状态为历史。

120已真实结束native_input_not_sent（2026-10-07T15:40:30.146128+00:00），SDK97160 seq18空闲登录保持。2628条隔离账SHAfe016aa4ff5acd48693ed18f0a44704acf893effb26711219c6c16e6d9025aa2及119前缀、109原未知均保留；原确认c7d6000dc5144e9e881ca10b446273b6确证未发送且已结束，未改旧终态或重发。120实机通过连续两个EX（20501/30501）有效扫描各6次→ALL→组队→胜利→奖励、连结专属关闭后新加入页→地图、刷新及五笔遗物购买完成关闭。实际余额720低于印记900，随机/选择印记未执行，119修正仍缺实机；完整离店/Boss/最终结算未验。根因不是整体预算耗尽、按钮识别或页面切换：按钮匹配.968/标题.927，动作限定探测遗漏所请求控制锚点，现有guard找不到控制；scene.py未被独立补丁改动，这是既有共享接线缺口。共享入口仅转发并重读已有物理锚点，覆盖12类普通动作/关闭与下一步别名，不新增guard、降低阈值或加3秒。13新增（12调用者＋120原生保存帧）及当前512受影响检查全部通过；公开24文件闭包503项收集，新增12通过1私有帧跳过；D/S和来源同步。原独立531及GUI编译证据复用，受Scene修改影响部分由512新检查覆盖，不称原1879全套通过。下一121在真实保存快照下验商店确认→地图、印记加入及Boss/最终，不替代最后同批两局；已撤销私有刷新/单队覆盖，只保留开发单局停止，真实设置文件不改。新entrySHA33718fe2c23c4d28e8e65f480805776d423ebee1771d533821db5f2d36f610ec。19/12/4和R38最终交付继续。依据E/isolation-after109/RUN120_REVIEW.json、SHARED_CONTROL120_REVIEW.json。

120已真实启动（2026-10-07T15:10:04.028111+00:00），formal7326a2f75ffb45e7aba11f2a8d7a1bec/native4b5f2b33091741ab8e435808026797e6；父SDK97160 seq18登录保持，launcher154024/原生128252归属已核。119独立真实撤退，原2262条账前缀和109旧未知保留。1061项功能与资源输入冻结，运行中不修改；本局目标为印记加入/商店地图后继、连结关闭、累计列表滚动、第二EX以及Boss/最终结算，不替代最后两局。依据E/isolation-after109/BRANCH_AFTER119_START_RECEIPT.json。

独立检查四类修复已在119结束后的安全空闲阶段按函数片段合入，118回调资源复用与117成员读回保留，D/S功能镜像和固定上游来源哈希同步完成。合并后受影响531项通过、0失败/错误/跳过，含独立13项和119真实保存帧；公开23文件依赖闭包收集通过，公开新增检查16通过/1私有帧跳过。GUI Release重建0错误/12既有警告，实际Core程序集验证中性目标次数1/2、2/2文案，Release依赖版本与原镜像一致；当前42项隔离实际控件编辑保存重开通过，人工窗口未验。原独立全量1879通过/12失败/305跳过保留，不宣称全套通过；Windows清理异常不作为业务修改。119随机印记初始/连续加入后继缺口共享修正原30秒/16步不变，回放通过、实机待验；114刷新300扣费及新货架续购、115遗物身份购买关闭已实机通过，完整印记加入/离店、第二EX、Boss重试和最终结算仍缺。SDK97160 seq17内存登录保持，109原未知及119原账不补终态/不重发。下一局120目标：印记加入后返回商店/地图、连结专属关闭不再重复关闭、列表累计滚动和第二EX复用，再推进Boss/最终结算；开局后冻结全部功能与资源。19/12/4、最后冻结同SDK同批两局及R38完整/精确37→38交付继续。证据E/isolation-after109/INDEPENDENT_MERGED_CHECKS.json、GUI_INDEPENDENT_BUILD_RECEIPT.json、SHARED_IMPRINT119_REVIEW.json。

# 固定上游完整覆盖清单（2026-10-07 当前执行）

119已于2026-10-07T14:43:26.449410+00:00真实结束shop_imprint_result_unknown，count0；SDK97160 seq17闲置内存登录保持。1061运行输入整局不变，2262条账SHA d78f17d0578af64a09c5bd934159c44c10548dd8063872d0440ec6943d6e2609及118前缀、109旧未知均保留。已实机命中114刷新同页报价300→ACK→余额2940到2640→新货架续购、115四笔遗物同身份购买和关闭，以及900币随机强化印记购买/结果关闭；未通过随机印记加入后继、离店、第二EX、Boss和最终结算。新缺口是关闭购买结果后的短暂商店背景被立即当成终态，随后的保存帧已实际出现角色加入；不是报价回归、识别失败或预算耗尽。MaaShop共享随机/选择印记路径已把既有加入/pending条件接到后继等待，原30秒/16步不变；119纯原生保存帧确认加入和身份，原职阶映射正确，测试断言文字错误已仅修测试。

安全空闲后，独立15文件补丁已按函数片段合入：最终确证未发送恢复纯观察及阶段保留、连结专属关闭后的正文变化读回、五类列表滚动的本段基线、GUI中性的目标次数文案。118回调资源复用和117成员读回完整保留；尚未同步S镜像/来源登记，受影响回归运行中。GUI构建首次因旧r24包缓存路径被清理与.NET8引用包缺失失败，正在按固定项目依赖恢复构建缓存，不改业务。独立全量1879通过/12失败/305跳过等有效结论复用，不称全套通过。下一实机须待必要检查、GUI构建和同步完成；119原局/账保持，未撤退或重发；19/12/4及最后冻结同批两局、R38交付继续。依据E/isolation-after109/RUN119_REVIEW.json、INDEPENDENT_MERGE_APPLIED.json、SHARED_IMPRINT119_REVIEW.json。


用户已授权接入独立黎明界检查与修复。已完整核REPORT/PATCH_MANIFEST/fixes及有效验证：四类缺陷仍存在，产品6文件匹配独立基线，encounters.py仅scan_effective与补丁相关，当前回调生命周期修正必须保留；117成员读回保持。119正在正常运行，功能/资源继续冻结；安全空闲后按函数片段合并，接入13项新增回归，重跑修改影响的结算/连结/角色五类列表及118生命周期验证，按已有GUI构建流程验证文案，再同步镜像与来源登记。独立全量1879通过/12失败/305跳过及186子测试、10项缺资料/旧夹具定点复核和2项Windows清理瞬态复核分别复用，不抹去原失败XML、不为清理异常改业务。19/12/4、后续实机及最后冻结同批两局/发布目标继续有效。合并规划依据E/isolation-after109/INDEPENDENT_INTEGRATION_PLAN.json，独立源.runtime/independent-labyrinth-debug-20261007。


118已于2026-10-07T14:00:21.563172+00:00真实结束 encounter_runtime_unavailable、count0；未命中商店。原生日志18441明确第二次固定 LabyrinthEncounter 回调注册被拒，发生在30503幽灵领主观察入口，未发详情或挑战。不是OCR、预算、ACK或业务读回失败；按新增EX适配器生命周期缺口登记，未发现114/115/117回归。118实际已通过普通战及20503 EX组队/有效效果扫描/战斗/奖励，16次所属动画推进、5角色选择/11加入关闭、4链接、5遗物、20603事件奖励及地图后继；不能代替全部分支。源码1061输入全局保持不变，1924条账SHA cf411168d2888d6864d1fcd5387ef2ffb899321ca4373be34baaa8ec3d3c969c，109旧未知原字节保持。

本次仅共享MaaEncounter构造器复用现有runtime_resource上的识别回调及其命名空间；适配器仍绑定本次tasker/stamp/预算，未缓存局身份、策略或结果，适用于连续EX、事件EX后继、重试及新局同Owner。固定回调白名单保持，无新资格守卫、阈值/等待/限制变更。旧源码原生离线重现第二次注册失败；修正后完整受影响44项通过，包括118真实保存帧生产observe_encounter连续新节点/重复观察/新适配器零输入读回，以及三次独立适配器详情打开→读回→关闭→挑战后继。新增测试夹具调用错误仅修测试，失败证据保留。下一局目标仍为114实际扣300刷新/新货架续购、115同商品身份完整购买后继，并验证第二EX可继续；首次实机通过逐项登记，不替代最终冻结同批两局。SDK97160 seq16内存登录保留，先同步及最终新增检查，再真实独立撤退118、隔离新局。依据E/isolation-after109/RUN118_REVIEW.json、SHARED_ENCOUNTER118_REVIEW.json。


本轮于2026-10-07T13:35:48.743438+00:00按用户指令恢复，优先减少商店实机返工。114实际刷新确认所需300/持有3160已定位为共享报价遗漏同行金额，原修正保留；115商品OCR差异是独立身份缺口，当前身份确认→购买→完成→关闭接线保留。完整核了遗物、随机/选择印记、刷新报价及所有后继，商店109项和原遗物图标8项有效验证按当前哈希复用，未重复修改。117暂停前成员修正已补缺失验证：当前受影响98项去重通过，其中117实际保存帧原生成员稳定读回4.75秒，小于原12秒；旧refine多裁剪导致测试调用数假设错误，只修测试，不改功能。普通/EX/Boss共享换人后继均已核。原117局尚未撤退、隔离1646条账及109旧未知保持，SDK97160 seq15闲置内存登录保持。接下来同步源码/来源登记后，先独立真实撤退117，再新隔离局；开局目标明确为114真实300刷新扣费/新货架续购、115同商品身份及117成员读回，并记录实际命中和新缺口/回归分类。运行中保持全部功能输入不变；本局不替代最后冻结同批两局。19/12/4及R38完整包、精确37→38补丁继续。依据E/isolation-after109/SHOP_QUOTE114_RECHECK_RECEIPT.json、SHARED_MEMBER_READBACK117_REVIEW.json；下面暂停与运行状态为历史。

用户于2026-10-07T13:25:21.650701+00:00明确暂停。开发、并行审查与离线检查停止；未启动118，未撤退117，SDK父97160/seq15内存登录保持且child_active=false。117原账、末笔1082成功输入ACK及失败业务读回不改，109旧未知原字节保留。暂停前D已落地117共享成员读回修正（character_recognition SHA850e03a6ac5350b17f5c91631a7b5a0753cf7e5f0146c4b8d620b113a44f702f；team_execution SHA2f89f8c74c85afc97889fac55060a47348496c285d5eb82373d4341d84ff2d69）：只在换人ACK后省略候选区匹配，复用真实成员识别，后续ADD重新full读取；原阈值、两帧稳定和12秒预算保持。独立代码审查PASS，但member117-affected检查被用户暂停中断，只有中间log、无完整XML，不声明通过；117新增测试尚待完整验收，D/S生产同步与来源登记未完成，发布SOURCE_TESTS仍8条、未加117。恢复时先核当前源码和测试完成度，补未完成检查与登记，再按原授权真实撤退117/隔离新验收；19/12/4、最终冻结同批两局及R38交付继续待完成。117根因证据见RUN117_REVIEW.json和RUN117_NATIVE_READ_TIMELINE.json；不按后续stop诱发Recognition.Failed错误判为真实识别失败。下文运行描述均为历史。

117已于2026-10-07T13:10:15.386297+00:00真实结束character_readback_unknown、计数0；SDK97160 seq15闲置且登录保持。1061项冻结输入未变；隔离1646条账SHAd5309cfea925ecd3bb0b5b57a87344f86f6baa5a694839b5844f0f33efd06982，116前缀及109旧未知原字节均保持。EX20502有效列表六次滚动已完成回ALL，去选1051/1075及加入1082有成功ACK；最后cfedaa20937d46a8b9be59e5e896d0d3仅证明输入，业务读回失败，不补写成功、不重发。21:10:11.193实际四成员已全可信，下一稳定读跨原预算；正在核共享成员读回耗时与后继，尚未修改或开启118。116候选冲突修正本局已继续通过。依据E/isolation-after109/RUN117_REVIEW.json；更早运行段落为历史。

当前116已真实结束native_input_not_sent（2026-10-07T12:35:35.253981+00:00、计数0），SDK97160 seq14闲置且登录保持。原隔离1462条账SHA549070138001a595d7ad8bc471037301da0f29e9f8d3da9566207b679b226853及109旧未知保持。EX20501有效列表17次滚动已完成回ALL；实际去选1059成功，下一去选1807的task200010829明确未发送。末帧复现候选区两卡同1051触发整页歧义，4实际成员均可信。已在唯一共享CharacterRecognizer.recognize把冲突候选降为Unknown并保留原候选证据；成员重复仍拒绝，冲突目标/开局冲突选中仍零输入，不改阈值、原预算和事务。当前180项既有调用者与6项新增保存帧/原生检查全部通过（186、无跳过），独立审查通过；新修正尚待实机。115商店身份及114刷新报价修正保持，但116尚未到商店，不补实机证明。下一步真实独立撤退116后新验收；19/12/4、冻结后同SDK同批最后两局及R38完整/精确37→38交付继续。回执E/isolation-after109/SHARED_CHARACTER_COLLISION116_REVIEW.json；下面115和更早状态为历史。

上一局114诊断及修正：114已结束于shop_quote_unknown；seq12 SDK父97160闲置且内存登录保留。formal deb8e7ba749c406c9d64c72e1b98f307/native050a5bff02924d759ba0c92d2e817faa，实际1002条隔离账SHA1fde66e858da74a9f090ddf6fcdc1ed6a1929d8e4781da1414a971c56cbd7b88。114刷新提案ACK与弹窗识别均完成；同帧明确所需300、持有3160，未产生刷新购买intent。根因是共享confirmation_terms只支持inline费用，遗漏所需金币同行金额，不是识别失败、预算耗尽或ACK未知。

当前修正：只在共享confirmation_terms补同帧严格标签右侧且纵向重叠的唯一金额；旧inline路径、商品/系列校验、fresh guard、原等待及阈值不变。缺失/重复/歧义/inline冲突均拒绝。现有购买、完成、随机/选择印记和刷新调用共用；固定上游EntryRecognitionSession:6920–7002提供刷新ACK/确认/商店后继，此金额解析为Maa本地适配，不冒称上游原样算法。当前受影响97项通过、无跳过，含114实际帧300/3160及原生fresh guard、模拟ACK后独立余额后继、旧54/72/81购买边界；回放不宣称真实刷新扣费。原113导航182项按源码未变复用。

114实机范围：原3秒开局、6场战斗/12页结算、20次所属动画推进及角色/羁绊/遗物奖励；EX30502详情→有效效果六次滚动→ALL→实际队伍→开战获胜；普通20301保留可信完整5人；商店三笔1350遗物购买→完成→返回货架。仍缺刷新真实扣300/新货架续购、随机/选择印记完整加入、Boss/失败重试及最终结算等必要实机分支，不能据局部通过升级整链。

历史114后启动记录：绑定114真实失败指针，经独立真实撤退后启动隔离新验收；root为唯一SDK执行者。109原10801条账SHA19b07ee1f9d2d3f2ce998726962c559a168066df81954957b97085ae0f800276、旧unknown19691f5d1f5c4da28b1cd53c24c80fee及113/114全部证据保留，不补终态、不重发旧输入。19配置/12链/4外层、可选临时难度5、重新冻结同SDK同批最终两局、R38完整包及精确37→38补丁继续；真实设置保持。证据E/isolation-after109/RUN114_REVIEW.json、SHARED_QUOTE114_REVIEW.json、refresh114-affected.xml。下文旧运行状态为历史。

固定commit `b8d8af912269cfea9ad65fd4e284d0d64da8e118`。这是现有 LABYRINTH_PLAN 的唯一逐项附件，沿用当前候选与固定快照；函数存在和资料导入不算接通或实机完成。33页面、13进入动作、全部31后进入动作、22事件/41选项、19参数、12共享链、4外层差异已列。

112历史归档：正式R35/安装R37/目标R38；19参数已接线。112已结束，formal543d76b1ea1a4c6491a8a259e648e2c0/nativead924cd85bea439391a0efd97bddb348，finished2026-10-07T09:32:13.618153+00:00，character_list_edge_unknown，计数0。隔离432条账全部真实终态；原109仍10801条原字节及末笔in_flight，未追补。112已实机通过原3秒开局、五场战斗、EX扫描前段、连续结算/角色/羁绊/遗物奖励及多次owned安全点；这些局部命中不能代表商店、Boss或最终结算完成。根因是有效角色列表回弹改变卡框/高亮和细微滚动位置，整页哈希无法辨识终点。五处终点调用统一角色身份/顺序、原滚动条.02容差；原签名诊断、24次上限、阈值和时间预算不变。三处可选同页下一批姓名比较共用单次原生Read；纯识别缺失回到原页面等待，Runtime未知/超时仍传播，必须加入确认不变。当前去重213项有效回放/检查通过，含109加入→111地图和112实际帧；旧替身缺少shared API的失败单独修正验证，不计失败XML全通过。六个功能文件之外SHA不变、指定函数之外AST相等。原用户选定两处上游行为继续；最新六文件尚待新实机。97160 SDK seq10 idle/内存保留。必要实机后恢复真实快照、冻结，同SDK同批started/cleared/cleared/finished计数1、2，再R38完整及精确37→38安装/两个桌面ZIP。证据E/isolation-after109/RUN112_REVIEW.json、SHARED_READ_MOTION112_RECEIPT.json。

本次用户明确改约束：两处均采用固定上游行为，允许拥有的召唤阶段UNKNOWN安全点推进，以及同事件已ACK选择6秒后仍同页时退休选项再选替项；只适用于这两项。没有盲选首卡/泛化确认/旧动作重发；阈值、原调用等待、100次/30分钟不变。5个可选最终模板原快照未提供，按真实最终动作阶段有界fallback，不伪造页面识别。

验收级别：**已接线**表示默认/配置/实际调用后继连接；**回放通过**只对应列出的有效XML/保存帧；**实机通过**只对应真实native/业务账命中；**真实缺口**逐项保留。108/109两页结算和确证not_sent后的纯读恢复属局部实机命中，角色奖励加入身份未完成，不能提升为完整奖励链。所属DirectHit安全点在111至116已有局部实机命中；最终动画仍待验。
## 19参数：默认/校验/GUI/保存重开/快照/实际决策

依据 `LabyrinthStrategySettings.kt:17-65/74-128`，GUI PcrLabyrinthSettings / PcrLabyrinthView，parse_settings→不可变Strategy。缺字段补固定上游默认；旧显式false/值保留，personal_scores仍保留。原十项是Boss模式1、三队权重3、五项系统评分5、buy_relics1。正式Desktop Release和Headless44检查通过，运行快照不随编辑改变。

| #／参数 | 字段／默认／校验 | GUI编辑→存盘重开 | 实际决策入口 | 验证／实机缺口 |
| --- | --- | --- | --- | --- |
| 1 | 玩家评分权重 · player_weight=70 · 0–100整数 | PlayerWeight→payload/GUI Save/new Settings | roles.py:293 (def configured_optimizer) | config48/Headless44；各决策离线专项；新实机待验 |
| 2 | 同属性加成开关 · attribute_pairing_bonus_enabled=True · 布尔 | AttributePairingBonusEnabled→payload/GUI Save/new Settings | roles.py:293 (def configured_optimizer) | config48/Headless44；各决策离线专项；新实机待验 |
| 3 | 重复职能扣分 · duplicate_role_penalty=8 · 0–30整数 | DuplicateRolePenalty→payload/GUI Save/new Settings | roles.py:293 (def configured_optimizer) | config48/Headless44；各决策离线专项；新实机待验 |
| 4 | 物法混编扣分 · mixed_damage_penalty=15 · 0–30整数 | MixedDamagePenalty→payload/GUI Save/new Settings | roles.py:293 (def configured_optimizer) | config48/Headless44；各决策离线专项；新实机待验 |
| 5 | 有效效果角色加分 · effective_character_system_bonus=8 · 0–30整数 | EffectiveCharacterSystemBonus→payload/GUI Save/new Settings | encounters.py:302 (def scan_effective) → roles.py:100 (def score) | config48/Headless44；各决策离线专项；新实机待验 |
| 6 | Boss纯伤害体系偏好 · prefer_pure_boss_damage_system=True · 布尔 | PreferPureBossDamageSystem→payload/GUI Save/new Settings | autoplay.py:639 (def _context) | config48/Headless44；各决策离线专项；新实机待验 |
| 7 | 单队Boss失败后转多队 · single_boss_fallback_to_multi_after_three_failures=True · 布尔 | SingleBossFallbackToMultiAfterThreeFailures→payload/GUI Save/new Settings | autoplay.py:1076 (switch=fallback) | config48/Headless44；各决策离线专项；新实机待验 |
| 8 | 转多队前重试次数 · single_boss_retry_count_before_multi=2 · 0–10整数 | SingleBossRetryCountBeforeMulti→payload/GUI Save/new Settings | autoplay.py:1076 (switch=fallback) | config48/Headless44；各决策离线专项；新实机待验 |
| 9 | 连续三次战败后重开 · reroll_after_three_battle_failures=True · 布尔 | RerollAfterThreeBattleFailures→payload/GUI Save/new Settings | autoplay.py:1077 (limit=max) → batch.py:303 (failure_verified) | config48/Headless44；各决策离线专项；新实机待验 |
| 10 | 补基础截止区域 · baseline_last_area=3 · 0–5整数 | BaselineLastArea→payload/GUI Save/new Settings | strategies.py:58 (def choose_relic) | config48/Headless44；各决策离线专项；新实机待验 |
| 11 | 基础目标层数 · baseline_target=4 · 1–4整数 | BaselineTarget→payload/GUI Save/new Settings | strategies.py:58 (def choose_relic) | config48/Headless44；各决策离线专项；新实机待验 |
| 12 | 转追弱体最低层数 · debuff_pivot_minimum=12 · 5–15整数 | DebuffPivotMinimum→payload/GUI Save/new Settings | strategies.py:58 (def choose_relic) | config48/Headless44；各决策离线专项；新实机待验 |
| 13 | 弱体落后容差 · debuff_pivot_tolerance=2 · 0–5整数 | DebuffPivotTolerance→payload/GUI Save/new Settings | strategies.py:58 (def choose_relic) | config48/Headless44；各决策离线专项；新实机待验 |
| 14 | 遗物印记优先级 · relic_mark_priority=[] · 五种印记有序子集，不重复；空沿用内置 | RelicMarkPriority→payload/GUI Save/new Settings | strategies.py:58 (def choose_relic) | config48/Headless44；各决策离线专项；新实机待验 |
| 15 | 商店刷新开关 · refresh_shop=True · Settings顶层布尔；旧明确False保留 | RefreshShop→payload/GUI Save/new Settings | shop_execution.py:334 (def run) | config48/Headless44；各决策离线专项；新实机待验 |
| 16 | 商店刷新起始区域 · refresh_from_area=5 · 1–5整数 | RefreshFromArea→payload/GUI Save/new Settings | shop_execution.py:334 (def run) | config48/Headless44；各决策离线专项；新实机待验 |
| 17 | 普通战胜率 · normal_win_rate=95 · 0–100整数 | NormalWinRate→payload/GUI Save/new Settings | event_execution.py:36 (planner=Planner) | config48/Headless44；各决策离线专项；新实机待验 |
| 18 | EX战胜率 · extreme_win_rate=70 · 0–100整数 | ExtremeWinRate→payload/GUI Save/new Settings | event_execution.py:36 (planner=Planner) | config48/Headless44；各决策离线专项；新实机待验 |
| 19 | 各公会自定义开局三人候选 · opening_rosters={} · 公会1–5，三个槽，每槽有序有效ID列表，全部候选不重复；缺省沿用上游 | Goals[].OpeningRoster→payload/GUI Save/new Settings | game_port.py:193 (slots=batch.settings.strategy.opening_rosters) → opening.py:43 (def invite) | config48/Headless44；各决策离线专项；新实机待验 |

## 33页面

| 页面 | 上游依据 | 当前执行入口 | 配置与实际后继 | 有效证据 | 真实缺口 |
| --- | --- | --- | --- | --- | --- |
| TITLE_WAITING_TAP | EntryClassifier/EntryPageState、EntrySession对应分派 | game_port.py:160 (def restore) | SDK/原主页导航 → 黎明界；沿用原登录/加载预算 | 历史106/GUI/导航有效证据复用；R36阈值不改 | 未增加日常实机全链声明 |
| GAME_LOADING_PROGRESS | EntryClassifier/EntryPageState、EntrySession对应分派 | game_port.py:160 (def restore) | SDK/原主页导航 → 黎明界；沿用原登录/加载预算 | 历史106/GUI/导航有效证据复用；R36阈值不改 | 未增加日常实机全链声明 |
| PRE_HOME_DATA_LOADING | EntryClassifier/EntryPageState、EntrySession对应分派 | game_port.py:160 (def restore) | SDK/原主页导航 → 黎明界；沿用原登录/加载预算 | 历史106/GUI/导航有效证据复用；R36阈值不改 | 未增加日常实机全链声明 |
| HOME_ANNOUNCEMENT | EntryClassifier/EntryPageState、EntrySession对应分派 | game_port.py:160 (def restore) | SDK/原主页导航 → 黎明界；沿用原登录/加载预算 | 历史106/GUI/导航有效证据复用；R36阈值不改 | 未增加日常实机全链声明 |
| HOME | EntryClassifier/EntryPageState、EntrySession对应分派 | game_port.py:160 (def restore) | SDK/原主页导航 → 黎明界；沿用原登录/加载预算 | 历史106/GUI/导航有效证据复用；R36阈值不改 | 未增加日常实机全链声明 |
| ADVENTURE | EntryClassifier/EntryPageState、EntrySession对应分派 | game_port.py:160 (def restore) | SDK/原主页导航 → 黎明界；沿用原登录/加载预算 | 历史106/GUI/导航有效证据复用；R36阈值不改 | 未增加日常实机全链声明 |
| DAWN_REALM_HOME_IDLE | EntryClassifier/EntryPageState、EntrySession对应分派 | batch.py:169 (def acquire) → acquisition.py:134 (def observe_existing_opening) | 真实TOP/RESUME/TOP，未推进匹配复用；否则实际retire→ENTER→handoff | config existing-final/两轮9项及原图11input；历史106 | 新既有开局复用待实机 |
| DAWN_REALM_HOME_ACTIVE | EntryClassifier/EntryPageState、EntrySession对应分派 | batch.py:169 (def acquire) → acquisition.py:134 (def observe_existing_opening) | 真实TOP/RESUME/TOP，未推进匹配复用；否则实际retire→ENTER→handoff | config existing-final/两轮9项及原图11input；历史106 | 新既有开局复用待实机 |
| INITIAL_CHARACTER_SELECTION | EntryClassifier/EntryPageState、EntrySession对应分派 | opening.py:43 (def invite)／rewards.py:144 (def choose_free) | 开局3人或事件/商店实数1/2 → invite ACK → 动画 → actual joined | 107原帧、native-impact03、final-shared09；opening原3秒native完整3项 | 33151/33152职阶及新选择印记待实机 |
| CHARACTER_JOINED | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:692 (def accept_joined) | actual IDs核pending并记owned → close → 下一joined/奖励/地图；召唤阶段有界推进 | joined/连续奖励共享检查；历史106 | 新连续批次待实机 |
| LINK_CHOICE | EntryClassifier/EntryPageState、EntrySession对应分派 | links.py:326 (def choose) | 原生选择/关闭ACK → 真实奖励后继；事件同页6秒替选按新授权，动画明确阶段按上游推进 | shared252+88+44范围、历史106；根animation-impact01 71项及business-impact01 62项 | 新增分支实机待验 |
| RELIC_CHOICE | EntryClassifier/EntryPageState、EntrySession对应分派 | relics.py:537 (    def choose(self) | 原生选择/关闭ACK → 真实奖励后继；事件同页6秒替选按新授权，动画明确阶段按上游推进 | shared252+88+44范围、历史106；根animation-impact01 71项及business-impact01 62项 | 新增分支实机待验 |
| EVENT_CHOICE | EntryClassifier/EntryPageState、EntrySession对应分派 | event_execution.py:27 (def run) | 原生选择/关闭ACK → 真实奖励后继；事件同页6秒替选按新授权，动画明确阶段按上游推进 | shared252+88+44范围、历史106；根animation-impact01 71项及business-impact01 62项 | 新增分支实机待验 |
| EVENT_ANIMATION | EntryClassifier/EntryPageState、EntrySession对应分派 | scene.py:398 (def wait_for) | 原生选择/关闭ACK → 真实奖励后继；事件同页6秒替选按新授权，动画明确阶段按上游推进 | shared252+88+44范围、历史106；根animation-impact01 71项及business-impact01 62项 | 新增分支实机待验 |
| ITEM_REWARD | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:887 (def _action_transition_readback) | 原生选择/关闭ACK → 真实奖励后继；事件同页6秒替选按新授权，动画明确阶段按上游推进 | shared252+88+44范围、历史106；根animation-impact01 71项及business-impact01 62项 | 新增分支实机待验 |
| SHOP | EntryClassifier/EntryPageState、EntrySession对应分派 | shop_execution.py:334 (def run) → purchase_once/_refresh/_imprint_following | 报价→购买确认→完成→补位/选择/实际加入→刷新300报价/后货架→后续买→退出地图 | 遗物历史实机；imprint/refresh共享专项；原shop回归 | 印记/刷新新实机待验 |
| SHOP_PURCHASE_CONFIRMATION | EntryClassifier/EntryPageState、EntrySession对应分派 | shop_execution.py:334 (def run) → purchase_once/_refresh/_imprint_following | 报价→购买确认→完成→补位/选择/实际加入→刷新300报价/后货架→后续买→退出地图 | 遗物历史实机；imprint/refresh共享专项；原shop回归 | 印记/刷新新实机待验 |
| SHOP_PURCHASE_COMPLETE | EntryClassifier/EntryPageState、EntrySession对应分派 | shop_execution.py:334 (def run) → purchase_once/_refresh/_imprint_following | 报价→购买确认→完成→补位/选择/实际加入→刷新300报价/后货架→后续买→退出地图 | 遗物历史实机；imprint/refresh共享专项；原shop回归 | 印记/刷新新实机待验 |
| SHOP_EXIT_CONFIRMATION | EntryClassifier/EntryPageState、EntrySession对应分派 | shop_execution.py:334 (def run) → purchase_once/_refresh/_imprint_following | 报价→购买确认→完成→补位/选择/实际加入→刷新300报价/后货架→后续买→退出地图 | 遗物历史实机；imprint/refresh共享专项；原shop回归 | 印记/刷新新实机待验 |
| NODE_SELECTION | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:937 (def _move) | 协议真实拓扑/原生AREA/可达定位→原生preview/确认→实际后继 | 历史实际路线及107branches/现有navigator回放 | 无header背景保留UNKNOWN，只有拥有奖励/最终阶段可推进 |
| NODE_MAP_VIEW | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:937 (def _move) | 协议真实拓扑/原生AREA/可达定位→原生preview/确认→实际后继 | 历史实际路线及107branches/现有navigator回放 | 无header背景保留UNKNOWN，只有拥有奖励/最终阶段可推进 |
| BATTLE_CHALLENGE | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:716 (def prepare_team) → encounters.py:274 (def observe_ex)／team_execution.py:198 (def prepare_boss) | 14EX/冰狼详情/有效空列表→配置评分→实际队伍→ownedStart→结果→有界新retry/排除/单转多/真实失败retire | battle114不同项、根默认三败/切multi整loop专项、历史多队Boss | 新增攻略/effective/失败切换实机待验 |
| BATTLE_TEAM_SELECTION | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:716 (def prepare_team) → encounters.py:274 (def observe_ex)／team_execution.py:198 (def prepare_boss) | 14EX/冰狼详情/有效空列表→配置评分→实际队伍→ownedStart→结果→有界新retry/排除/单转多/真实失败retire | battle114不同项、根默认三败/切multi整loop专项、历史多队Boss | 新增攻略/effective/失败切换实机待验 |
| BATTLE_IN_PROGRESS | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:716 (def prepare_team) → encounters.py:274 (def observe_ex)／team_execution.py:198 (def prepare_boss) | 14EX/冰狼详情/有效空列表→配置评分→实际队伍→ownedStart→结果→有界新retry/排除/单转多/真实失败retire | battle114不同项、根默认三败/切multi整loop专项、历史多队Boss | 新增攻略/effective/失败切换实机待验 |
| BATTLE_FAILED | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:716 (def prepare_team) → encounters.py:274 (def observe_ex)／team_execution.py:198 (def prepare_boss) | 14EX/冰狼详情/有效空列表→配置评分→实际队伍→ownedStart→结果→有界新retry/排除/单转多/真实失败retire | battle114不同项、根默认三败/切multi整loop专项、历史多队Boss | 新增攻略/effective/失败切换实机待验 |
| BATTLE_RESULT | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:716 (def prepare_team) → encounters.py:274 (def observe_ex)／team_execution.py:198 (def prepare_boss) | 14EX/冰狼详情/有效空列表→配置评分→实际队伍→ownedStart→结果→有界新retry/排除/单转多/真实失败retire | battle114不同项、根默认三败/切multi整loop专项、历史多队Boss | 新增攻略/effective/失败切换实机待验 |
| RUN_CLEAR_RESULT | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:815 (class AutoplayRunner) → scene.py:398 (def wait_for) | owned最终Boss胜利→score/CLEAR/动画/chest/item→home→协议TOP0后计数；UNKNOWN最终driver仅实际阶段 | 7张固定原最终页、106完整CLEAR、根71影响；final-settlement/final.xml102项；driver上下文专项 | 5张可选模板原上游缺失；有界阶段fallback替代，不伪造识别，最终同批2局待验 |
| RUN_CLEAR_CONGRATULATIONS | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:815 (class AutoplayRunner) → scene.py:398 (def wait_for) | owned最终Boss胜利→score/CLEAR/动画/chest/item→home→协议TOP0后计数；UNKNOWN最终driver仅实际阶段 | 7张固定原最终页、106完整CLEAR、根71影响；final-settlement/final.xml102项；driver上下文专项 | 5张可选模板原上游缺失；有界阶段fallback替代，不伪造识别，最终同批2局待验 |
| RUN_CLEAR_CHARACTER_SUMMARY | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:815 (class AutoplayRunner) → scene.py:398 (def wait_for) | owned最终Boss胜利→score/CLEAR/动画/chest/item→home→协议TOP0后计数；UNKNOWN最终driver仅实际阶段 | 7张固定原最终页、106完整CLEAR、根71影响；final-settlement/final.xml102项；driver上下文专项 | 5张可选模板原上游缺失；有界阶段fallback替代，不伪造识别，最终同批2局待验 |
| RUN_CLEAR_REWARD_ANIMATION | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:815 (class AutoplayRunner) → scene.py:398 (def wait_for) | owned最终Boss胜利→score/CLEAR/动画/chest/item→home→协议TOP0后计数；UNKNOWN最终driver仅实际阶段 | 7张固定原最终页、106完整CLEAR、根71影响；final-settlement/final.xml102项；driver上下文专项 | 5张可选模板原上游缺失；有界阶段fallback替代，不伪造识别，最终同批2局待验 |
| RUN_CLEAR_CHEST_ANIMATION | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:815 (class AutoplayRunner) → scene.py:398 (def wait_for) | owned最终Boss胜利→score/CLEAR/动画/chest/item→home→协议TOP0后计数；UNKNOWN最终driver仅实际阶段 | 7张固定原最终页、106完整CLEAR、根71影响；final-settlement/final.xml102项；driver上下文专项 | 5张可选模板原上游缺失；有界阶段fallback替代，不伪造识别，最终同批2局待验 |
| RUN_CLEAR_CHEST_RESULT | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:815 (class AutoplayRunner) → scene.py:398 (def wait_for) | owned最终Boss胜利→score/CLEAR/动画/chest/item→home→协议TOP0后计数；UNKNOWN最终driver仅实际阶段 | 7张固定原最终页、106完整CLEAR、根71影响；final-settlement/final.xml102项；driver上下文专项 | 5张可选模板原上游缺失；有界阶段fallback替代，不伪造识别，最终同批2局待验 |
| UNKNOWN | EntryClassifier/EntryPageState、EntrySession对应分派 | autoplay.py:815 (class AutoplayRunner) → scene.py:398 (def wait_for) | 只有拥有的角色/奖励/最终阶段按上游有界推进；其它未知不补点；事件真实同页6秒替选独立记账 | 2026-10-07用户明确选择两处均按上游；新共享检查 | 实机阶段待验 |

## 13进入动作（逐项）

| 动作 | 上游依据 | 当前入口 | 实际执行与后继 | 验证／缺口 |
| --- | --- | --- | --- | --- |
| TITLE_CONTINUE | LabyrinthEntryActionPlanner.kt:74 | game_port.restore → Maa startup/session | 实际标题→加载→公告/主页；沿用原native标题登录等待 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| PRE_ANNOUNCEMENT_CONTINUE | LabyrinthEntryActionPlanner.kt:75 | game_port.restore → existing startup | 原始加载/下载流程，客户端正常更新不记代码错误 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| CLOSE_ANNOUNCEMENT | LabyrinthEntryActionPlanner.kt:76 | game_port.restore → existing native announcement close | 明确公告→主页，不泛化确认 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| OPEN_ADVENTURE | LabyrinthEntryActionPlanner.kt:77 | game_port.restore → resume_protocol/session | 实际主页→冒险页→黎明界 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| OPEN_DAWN_REALM | LabyrinthEntryActionPlanner.kt:78 | game_port.restore → session.resume_home_pipeline | 实际黎明界入口→TOP核验 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| START_DAWN_REALM | LabyrinthEntryActionPlanner.kt:79 | Batch.acquire → ProtocolPort.create → GamePort.restore | 真实ENTER独立创建→原生交接，不点击盲创第二局 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| RESUME_DAWN_REALM | LabyrinthEntryActionPlanner.kt:80 | ProtocolPort.observe_existing_opening → GamePort.restore | 真实匹配未推进开局或新创建自己的enter；半途旧局实际撤退替代 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| SELECT_INITIAL_CHARACTER | LabyrinthEntryActionPlanner.kt:81 | opening.MaaOpening.invite → Characters ToggleOnce | 公会自定义三人候选/原评分择优；明确角色身份及已选读回 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| SCROLL_INITIAL_CHARACTERS | LabyrinthEntryActionPlanner.kt:82 | opening.invite → character_recognition candidates/scroll | 复用已有角色列表导航和有界扫描；不用首卡盲选 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| INVITE_INITIAL_CHARACTERS | LabyrinthEntryActionPlanner.kt:83 | opening.invite / invite_gourmet | 真实选满3→InviteOnce ACK→原3秒内actual joined读回 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| ADVANCE_CHARACTER_ACQUISITION | LabyrinthEntryActionPlanner.kt:84 | opening._wait_invitation_joined → scene.wait_for | 拥有邀请ACK后按固定safe-area驱动，原等待不延长 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| CLOSE_CHARACTER_JOINED | LabyrinthEntryActionPlanner.kt:85 | opening.consume_granted / invite → original native close | 实际角色加入/赠送身份→一次关闭→地图/继续邀请 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |
| CLOSE_ITEM_REWARD | LabyrinthEntryActionPlanner.kt:86 | game_port restore departure reward / opening native successor | 仅明确奖励→关闭→actual opening/map，不伪造库存 | 原SDK/启动/开局实际证据复用；新既有开局及邀请动画待实机 |

## 固定上游后进入动作（逐项，不用泛化确认替代）

| 动作 | 上游依据 | 当前入口 | 后继/执行边界 | 证据／缺口 |
| --- | --- | --- | --- | --- |
| GENERIC | LabyrinthPostEntryActionKind.kt:13 | scene.perform_scene_action | 仅实际页面控件，不能泛化确认 | 对应共享链离线检查见下；新增实机待验 |
| SELECT_ROLE_REWARD | LabyrinthPostEntryActionKind.kt:16 | rewards.choose | 下轮三选一/召唤/actual joined | 对应共享链离线检查见下；新增实机待验 |
| CLOSE_CHARACTER_JOINED | LabyrinthPostEntryActionKind.kt:17 | autoplay.accept_joined/_do(close_reward) | 下一joined/召唤/奖励/地图 | 对应共享链离线检查见下；新增实机待验 |
| ADVANCE_CHARACTER_ACQUISITION | LabyrinthPostEntryActionKind.kt:18 | scene.wait_for(acquisition_fields) | 原scope UNKNOWN/event_animation → 实际joined，40/.65s/原deadline；实际推进上下文可跨item，plain event item不伪造拥有 | 对应共享链离线检查见下；新增实机待验 |
| SELECT_EVENT | LabyrinthPostEntryActionKind.kt:21 | event_execution.run | 全部41选项后继；同页6s排除替选，目录最多3个不同choice，整体原30s | 对应共享链离线检查见下；新增实机待验 |
| ADVANCE_EVENT_ANIMATION | LabyrinthPostEntryActionKind.kt:22 | autoplay.play/_do(skip_event_animation) | 下一event/角色/遗物/币/普通/EX/地图 | 对应共享链离线检查见下；新增实机待验 |
| EVENT_FREE_ROLE_SELECT | LabyrinthPostEntryActionKind.kt:23 | rewards.choose_free | 实际分母1/2/3+ToggleOnce→计数读回 | 对应共享链离线检查见下；新增实机待验 |
| EVENT_FREE_ROLE_CONFIRM | LabyrinthPostEntryActionKind.kt:24 | rewards.choose_free | InviteOnce→驱动→actual joined | 对应共享链离线检查见下；新增实机待验 |
| CONFIRM_GENERIC_DIALOG | LabyrinthPostEntryActionKind.kt:26 | scene.close_reward | 仅“无法获得报酬”明确标题正文确认 | 对应共享链离线检查见下；新增实机待验 |
| SELECT_RELIC | LabyrinthPostEntryActionKind.kt:29 | relics.MaaRelicReward.choose | 实际层数/主追→选择→结果→关闭 | 对应共享链离线检查见下；新增实机待验 |
| SELECT_LINK_IMPRINT | LabyrinthPostEntryActionKind.kt:30 | links.MaaLinkReward.choose + autoplay.choose_reward | 实际imprint数量→结果关闭→召唤驱动→actual joined | 对应共享链离线检查见下；新增实机待验 |
| CLOSE_ITEM_REWARD | LabyrinthPostEntryActionKind.kt:31 | autoplay._do(close_reward) | owned奖励后继；最终item关闭后只等home | 对应共享链离线检查见下；新增实机待验 |
| SHOP_BUY_RELIC | LabyrinthPostEntryActionKind.kt:34 | shop_execution.purchase_once | 独立purchase proposal | 对应共享链离线检查见下；新增实机待验 |
| SHOP_BUY_IMPRINT | LabyrinthPostEntryActionKind.kt:35 | shop_execution.purchase_once | 随机/选择印记真实name/price proposal | 对应共享链离线检查见下；新增实机待验 |
| SHOP_CONFIRM_RELIC | LabyrinthPostEntryActionKind.kt:36 | batch.purchase/shop_execution.purchase_readback | same quote→actual completed | 对应共享链离线检查见下；新增实机待验 |
| SHOP_CONFIRM_IMPRINT | LabyrinthPostEntryActionKind.kt:37 | shop_execution.purchase_once/purchase_readback | same quote→completed→角色选择/actual加入/回店 | 对应共享链离线检查见下；新增实机待验 |
| SHOP_CLOSE_PURCHASE_COMPLETE | LabyrinthPostEntryActionKind.kt:38 | shop_execution.purchase_readback | actual货架/完整imprint following | 对应共享链离线检查见下；新增实机待验 |
| SHOP_REFRESH | LabyrinthPostEntryActionKind.kt:39 | shop_execution._refresh | 实际300/balance→proposal | 对应共享链离线检查见下；新增实机待验 |
| SHOP_CONFIRM_REFRESH | LabyrinthPostEntryActionKind.kt:40 | shop_execution._refresh | run_coins300→余额-300→actual货架→续购 | 对应共享链离线检查见下；新增实机待验 |
| SHOP_CLOSE | LabyrinthPostEntryActionKind.kt:41 | shop_execution.run 内 leave | 实际close→exit confirmation | 对应共享链离线检查见下；新增实机待验 |
| SHOP_CONFIRM_EXIT | LabyrinthPostEntryActionKind.kt:42 | shop_execution.run 内 leave | actual map | 对应共享链离线检查见下；新增实机待验 |
| BATTLE_START_CHALLENGE | LabyrinthPostEntryActionKind.kt:45 | autoplay.observe_encounter/_do(challenge) | 详情攻略/关闭→team | 对应共享链离线检查见下；新增实机待验 |
| BATTLE_RETRY | LabyrinthPostEntryActionKind.kt:46 | autoplay.play/team_execution | owned defeat→新retry ACK→排除实际失败队 | 对应共享链离线检查见下；新增实机待验 |
| BATTLE_RETRY_SWITCH_MULTI | LabyrinthPostEntryActionKind.kt:47 | autoplay.play/team_execution.prepare_boss(force_multi) | 配置阈值→真实切换ACK→新mode retry=0、旧mode排除清空 | 对应共享链离线检查见下；新增实机待验 |
| BATTLE_RESULT_NEXT | LabyrinthPostEntryActionKind.kt:48 | autoplay._do(settlement_next) | owned won后下一奖励 | 对应共享链离线检查见下；新增实机待验 |
| BOSS_SETTLEMENT_NEXT | LabyrinthPostEntryActionKind.kt:49 | autoplay._owned_boss_summary_scene/_do | owned Boss+实际next锚点优先 | 对应共享链离线检查见下；新增实机待验 |
| EX_PROBE_DETAIL | LabyrinthPostEntryActionKind.kt:52 | encounters.MaaEncounter.observe_ex | actual name/layout不足时原生details | 对应共享链离线检查见下；新增实机待验 |
| EX_CLOSE_DETAIL | LabyrinthPostEntryActionKind.kt:53 | encounters.MaaEncounter.observe_ex | actual close ACK→challenge读回 | 对应共享链离线检查见下；新增实机待验 |
| CLOSE_FINAL_SCORE | LabyrinthPostEntryActionKind.kt:56 | autoplay._do(settlement_next) | 实际score关闭ACK→CHEST阶段 | 对应共享链离线检查见下；新增实机待验 |
| CLOSE_FINAL_ITEM | LabyrinthPostEntryActionKind.kt:57 | autoplay._do(close_reward) | 实际final item关闭ACK→WAIT_HOME纯读 | 对应共享链离线检查见下；新增实机待验 |
| ADVANCE_FINAL_SETTLEMENT | LabyrinthPostEntryActionKind.kt:58 | scene.wait_for(final fields) + autoplay phase | 真实before_score/chest阶段，1.5s/max40，原budget内 C/R点位 | 对应共享链离线检查见下；新增实机待验 |

## 22事件/41选项及全部后继类别

共同依据 `events.placeholder.json` 与 `LabyrinthEventChoicePlanner.kt`，实际目录 `assets/labyrinth/event-recognition.json`。随机结果以实际页面为准，不按预期伪造库存；连续事件以新的event_id拥有下一次选择。

| 事件ID | 选项ID | 选择语义 | 允许且实际接入的后继 | 调用链 | 验证／缺口 |
| --- | --- | --- | --- | --- | --- |
| 1101 | 11011 | 想要坦克型 成为伙伴！；坦克型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1101 | 11012 | 想要治疗型 成为伙伴！；治疗型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1102 | 11021 | 想要强化型 成为伙伴！；强化型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1102 | 11022 | 想要增益型 成为伙伴！；增益型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1103 | 11031 | 想要减益型 成为伙伴！；减益型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1103 | 11032 | 想要干扰型 成为伙伴！；干扰型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1104 | 11041 | 想要攻击型 成为伙伴！；攻击型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1104 | 11042 | 想要破防型 成为伙伴！；破防型职能的 随机印记×2 ＋阿尔法金币×200 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1105 | 11051 | 一试身手吧！；向战斗格子（普通难度）的 敌人发起挑战 ＋阿尔法金币×200 | normal_challenge→实际ownedBattle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1105 | 11052 | 与强敌战斗真是令人兴奋！；向战斗格子（极难）的 敌人发起挑战 ＋阿尔法金币×300 | ex_challenge→14EX details/effective→实际Battle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1107 | 11073 | 摸一下 应该没关系吧？；有50％的概率获得阿尔法金币×1500 有50％的概率获得阿尔法金币×2000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1107 | 11072 | 噗吉 全力挥～击☆；有50％的概率获得随机职能的 选择印记×1 有50％的概率获得全体选择印记×1 | free_role_reward(实际1/2)→invite→动画→actual joined | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1107 | 11071 | 靠近的时候可得小心点；有50％的概率获得★2迷宫遗物×1 有50％的概率获得★3迷宫遗物×1 | actual item正文/显式mark+bonus→独立库存；未知名不猜，MAP共识仅低源或下次报价/人工详情校准 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1108 | 11082 | 难道不是左边吗？；阿尔法金币×1000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1108 | 11081 | 当然是右边啊！；有50％的概率获得★1迷宫遗物×1 有50％的概率获得随机职能的 随机印记×2 | item_reward/召唤→actual joined(不按印记数量伪造角色) / actual item正文/显式mark+bonus→独立库存；未知名不猜，MAP共识仅低源或下次报价/人工详情校准 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1109 | 11091 | 一试身手吧！；向战斗格子（普通难度）的 敌人发起挑战 ＋阿尔法金币×300 | normal_challenge→实际ownedBattle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1109 | 11092 | 与强敌战斗真是令人兴奋！；向战斗格子（极难）的 敌人发起挑战 ＋阿尔法金币×450 | ex_challenge→14EX details/effective→实际Battle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1110 | 11101 | 石头！！！；胜利后获得阿尔法金币×3000 失败后获得阿尔法金币×1000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1110 | 11102 | 出剪刀吧；胜利后获得阿尔法金币×3000 失败后获得阿尔法金币×1000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1110 | 11103 | 要不要出布呢；胜利后获得阿尔法金币×3000 失败后获得阿尔法金币×1000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1111 | 11111 | 弟弟 这边这边♪；阿尔法金币×2000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1111 | 11112 | 哥哥 在这边哦♪；★1～3迷宫遗物×１ | actual item正文/显式mark+bonus→独立库存；未知名不猜，MAP共识仅低源或下次报价/人工详情校准 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1112 | 11121 | 见识下御久间流的 剑术吧！；阿尔法金币×2000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1112 | 11122 | 这里就 交给我们吧！；纯成为伙伴 ＋全体随机印记×2～4 | item_reward/召唤→actual joined(不按印记数量伪造角色) | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1113 | 11131 | 一试身手吧！；向战斗格子（普通难度）的 敌人发起挑战 ＋阿尔法金币×400 | normal_challenge→实际ownedBattle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1113 | 11132 | 与强敌战斗真是令人兴奋！；向战斗格子（极难）的 敌人发起挑战 ＋阿尔法金币×600 | ex_challenge→14EX details/effective→实际Battle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1117 | 11171 | 一试身手吧！；向战斗格子（普通难度）的 敌人发起挑战 ＋阿尔法金币×500 | normal_challenge→实际ownedBattle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1117 | 11172 | 与强敌战斗真是令人兴奋！；向战斗格子（极难）的 敌人发起挑战 ＋阿尔法金币×750 | ex_challenge→14EX details/effective→实际Battle→奖励/地图 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1314 | 13141 | 悠闲地 钓鱼吧；有50％的概率获得★1迷宫遗物×1 并进入下一个事件 | actual item正文/显式mark+bonus→独立库存；未知名不猜，MAP共识仅低源或下次报价/人工详情校准 / actual新event_id→重新Planner，不重发旧选择 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1315 | 13151 | 大家一起 做料理吧☆；有50％的概率进入下一个事件 | actual新event_id→重新Planner，不重发旧选择 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 1316 | 13161 | 给你们点颜色瞧瞧！；有50％的概率获得阿尔法金币×2000 并进入下一个事件 | actual item/无法获得报酬→关闭 / actual新event_id→重新Planner，不重发旧选择 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 2314 | 23141 | 再挑战一次！；25％时可获得★2迷宫遗物×1 并进入下一个事件 | actual item正文/显式mark+bonus→独立库存；未知名不猜，MAP共识仅低源或下次报价/人工详情校准 / actual新event_id→重新Planner，不重发旧选择 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 2315 | 23152 | 这样应该就 足够了吧？；随机职能的 随机印记×2 | item_reward/召唤→actual joined(不按印记数量伪造角色) | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 2315 | 23151 | 我还没 吃够呢☆；有50％的概率进入下一个事件 | actual新event_id→重新Planner，不重发旧选择 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 2316 | 23162 | 到这里就行了……；挑战结束 | map/无报酬实际确认 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 2316 | 23161 | 今天真是走运！；有50％的概率获得阿尔法金币×4000 并进入下一个事件 | actual item/无法获得报酬→关闭 / actual新event_id→重新Planner，不重发旧选择 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 3314 | 33141 | 最后再较量一次！；10％时可获得★3迷宫遗物×1 | actual item正文/显式mark+bonus→独立库存；未知名不猜，MAP共识仅低源或下次报价/人工详情校准 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 3315 | 33152 | 这么多 根本吃不完吧！；随机职能的 选择印记×2 | free_role_reward(实际1/2)→invite→动画→actual joined | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 3315 | 33151 | 诶嘿嘿～ 再一下就好……；有50％的概率获得全体选择印记×２ | free_role_reward(实际1/2)→invite→动画→actual joined | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 3316 | 33162 | 差不多该放弃了……；挑战结束 | map/无报酬实际确认 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |
| 3316 | 33161 | 今天的我 才不会输！；有50％的概率获得阿尔法金币×8000 | actual item/无法获得报酬→关闭 | event_observation.catalogue→Planner→MaaEvent.run→Autoplay公共后继 | 22/41身份/效用＋原生event读回；具体新随机结果实机待验 |

## 12共享执行链

| # | 链 | 上游依据 | 当前执行入口 | 配置/后继 | 有效证据 | 真实缺口 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 全列表/职阶选1/2 | RoleRewardChoicePlanner:132；EntrySession:4318 | rewards.choose_free / characters ToggleOnce+InviteOnce | 11072/33151/33152及shop共用；当前实际列表不切ALL | 107原帧；free1/2/3、邀请/错加入/Stop共享专项 | 职阶与shop选择待实机 |
| 2 | 多轮三选一及连续加入 | EntrySession:4584/4714 | rewards.choose(deferred)→role_pending→accept_joined | remaining/新candidate为新轮，只有actualjoined加owned | final-shared09及joined整链 | 真实多轮批次待验 |
| 3 | 召唤/奖励/最终主动动画 | EntrySession:269/4598/5833 | scene.wait_for + opening/rewards/links/event/shop/autoplay | 已发ACK拥有阶段；角色40/.65s、最终40/1.5s；原budget不增 | 已接线；新33模块及边界当前590项回放，8保存帧边界/原身份读回；历史108/109只局部实机命中 | 114所属动画20次实机；最终动画及完整奖励批次仍待；109原未知不追认 |
| 4 | 事件拒绝替选 | EntrySession:5080-5111；PageRuntimePolicy:729 | MaaEvent.run→Planner.decide(rejected) | 本次授权same event6s超时推断拒选，最多3个不同choice/整体30s；UNKNOWN其它结果不重发 | upstream-event08 37、final-shared09 44 | 实际游戏拒绝替选待验 |
| 5 | 商店随机/选择印记 | ShopPolicy:42/79-146 | MaaShop.purchase_once/_imprint_following | 报价/局内币/完成→choose_free或random→actual加入→货架；初始/连续短SHOP背景等待既有joined/pending条件 | 已接线；531合并受影响检查含119保存帧及随机/选择连续加入反例通过 | 119随机900购买/完成关闭实机通过；加入→货架→地图和选择印记仍待，120验收中 |
| 6 | 刷新及续购 | EntrySession:6921 | MaaShop._refresh/run | refresh bool＋refresh_from_area，300报价/余额减300/新货架 | 已接线/回放通过；119所需300同帧报价→刷新ACK→2940减至2640→新ID暴击弧弓购买1350实机通过 | 刷新与续购实机通过；同次完整离店/地图仍待印记加入后继，不能升级整条商店链完成 |
| 7 | 14EX/冰霜魔狼 | ExEncounterStrategy:45/116/169/196/438 | encounters.observe_ex/boss_encounter→Context.encounter | 实际详情/1,2,5targets/现有角色能力机制评分 | battle114不同项；114EX30502详情/组队/获胜实机 | 其余策略/Boss分支按必要实机待验 |
| 8 | 有效效果包括空列表 | EntrySession:3100/3137 | encounters.scan_effective→Context.effective_ids→Optimizer | filter实际扫描/明确空→ALL恢复，空保持() | battle/native空notice专项；114有效列表6滚动→ALL→开战获胜实机 | 实际空列表/Boss分支待验 |
| 9 | 有界重试和排除 | BattleTeamRecommendation:195/295；EntrySession:197/6005 | autoplay.record_defeat/play→MaaTeam.retry_best | 只有owned defeat，排除全部实际started队，二败EX治疗；新尝试独立ACK | battle及根默认三败loop | 实机败后重试待验 |
| 10 | 单队Boss转多队 | EntrySession:208/4740/6013 | autoplay.switch→MaaTeam.prepare_boss(force_multi) | 默认2次重试后第三败，切换ACK再resetmode计数/排除 | 根switch6战整loop＋native多队 | 实机单切多待验 |
| 11 | 耗尽真实失败/撤退/续批 | EntrySession:226/232/4754/4774 | Autoplay.failure_verified→Batch.failed→Port.resume_failed_run→retire→TOP0 | 批次所有真实耗尽终态，clears不加/attempts加；false toggle仍批次limit耗尽撤退 | outer/failure SDK调用专项 | 实际失败撤退续批待验 |
| 12 | 实际层数/跨奖励商店主追 | NodeRelicStackResolver:207-244；CompactCounter:固定.40/.08；RelicStackLedger:25-42；EntrySession:2588-2609/3846/5140 | autoplay.calibrate_map/item_scene / relic_offers.current / shop.current_stacks→locked_focus | 5mark/5槽actual MAP两读一致，未知None；MAP不覆choice/acquisition，冲突保留；独立结果+bonus≤30；基础补足不擦focus | business18纯47＋保存帧MAP两读2/1及ITEM20零输入、根同值来源/连续事件/实际正文专项 | 新跨奖励/商店实机待验；随机未知名不猜mark/bonus，强源冲突等实际报价或人工详情 |

## 4外层差异

| 差异 | 上游依据 | 当前入口 | 行为/配置/后继 | 有效证据 | 边界或缺口 |
| --- | --- | --- | --- | --- | --- |
| 未推进既有开局复用 | RerollWorkflow:460-501；Controller:161-170 | Batch.first_acquire→Port.observe_existing_opening→same retained source→game_port | TOP/RESUME/TOP实数完整未推进匹配；不伪造ENTER | 新增9项及原图11input | 新实机待验 |
| 启动处理既有局 | RerollWorkflow:36/460；Controller:161 | Batch.acquire | 未推进匹配复用，已推进/不匹配实际retire→新ENTER；旧unknown journal仍block | outer既有局检查/历史开发sample原账保持 | 已接线及outer回放；生产启动处理既有局实机待验。私有隔离退局不代替此证明；未知旧动作不重发，任意半途不恢复角色遗物 |
| 按尝试次数批次目标 | AutoRunWorkflow:68-99与EntrySession批次耗尽终态 | Goal.mode / GUI / Batch.run | clears兼容旧cfg，attempts只计实际clear或failure_verified终态，未知不计 | GUI goal持久化、outer混合failed+cleared | 配置可用；新模式实机待验 |
| 安全阶段恢复批次 | RerollWorkflow checkpoint:181；Controller:1187-1196 | RunRecord.checkpoint/recover→OwnedRuntime Batch(resume=True) | 仅ready/绑定+cfg完全一致/原rows重算真实counts；playing/acquiring及finished不恢复 | outer ready/拒绝假count/path/混合attempts | 已接线及outer回放；ready checkpoint生产恢复实机待验。无任意半途恢复；保留现场及授权实际retire/新开局替代 |

## 验证与冻结/交付

已有未变回放按指纹复用；新的 config/GUI、battle、reward/shop回执分别 `O/config-wiring-20261007/CONFIG_WIRING_RECEIPT.json`、`E/battle/COVERAGE.json`、`O/shared-reward-shop-20261007/SHARED_WIRING_RECEIPT.json`。根受影响检查 `E/animation-impact01.xml`71通过和`E/business-impact01.xml`62通过；最终/原3s回放及新增边界定点验证另记，旧夹具差异保留首次XML且定点后验覆盖。临时目录WinError145单列环境清理异常，不能抹掉或当业务通过。

下一步先必要实机新分支，之后再冻结当前全部功能/资源/GUI，实际同SDK、同批两局只接受started/cleared/cleared/finished和计数1、2。功能再改重新两局。正式安装/精确补丁/全新解压/源码映射及桌面最终两个ZIP仍待，不能提前填完成。

第108局实机：创建108、真实开局三人、普通战开始/获胜下一步ACK，原账10706字节前缀保持。owned UNKNOWN→fresh WIN获得道具页的安全中心输入原生确认not_sent，原等待未继续读回；仅共享wait_for已修正，62项（含实际保存帧两个驱动零输入回放）通过，未重发首个ACK，阈值/guard/点位/预算不变。证据 O/RUN108_REVIEW.json、E/RUN108_SHARED_TRANSITION_AUDIT.json、E/owned-animation-refresh/RECEIPT.json；这些回放不等于新分支实机完成。

同一动画回归收口：9组shared caller详见 E/SHARED_ANIMATION_REGRESSION_CALLERS.json。按固定上游只把owned角色/最终UNKNOWN安全点的重复整页guard改为DirectHit，保留第一次观察、既有ACK归属、点位、max_hit、40次/间隔和原3/12/20/30秒调用预算，后继/角色/库存/完成仍真实读回；其他控件guard不改。33模块566项通过（opening/free1/2/multi/link/event/shop/refresh/final/unknown/partial），追加边界50项通过，去重590项。8个实际108/109保存帧回放：观察UNKNOWN后输入前已变页只做一次SavedImages原生安全点；首读已知后继零推进；109原角色reader独立读出1191，四次均原12秒内且不改owned/库存。证据E/owned-animation-upstream-safe/RECEIPT.json及ROOT_SHARED_REGRESSION_REVIEW.json；这些是回放，不是新实机。109的离线原生日志只证明forensic_no_native_action_observed，未返回实时SDK receipt，原OID19691f5d1f5c4da28b1cd53c24c80fee和in_flight不改。证据 O/RUN109_REVIEW.json、E/RUN109_OFFLINE_NATIVE_AUDIT.json；最终两局和交付仍未开始。


### 110隔离开局读回补充

已接线：共享MaaOpening._wait_invitation_joined对已知邀请源页直接交原生姓名读回，普通配置/美食殿堂两调用者共用；不会将源页认作加入成功。仍须原姓名和实际ID确认后才记owned/关闭。原3秒总预算、UNKNOWN拥有动画推进、所有输入及阈值保持。回放：两110实际加入保存帧与既有选三帧，零游戏连接；原姓名reader1.234/1.235秒、两个调用者实际ID1059/1075/1351；专项10通过、关联61通过。5项旧native断言失败以修改前模块同项复现并登记，不能算通过。实机：110只证明邀请ACK与源页→joined变页，原姓名任务未开始识别，不算开局整链通过；修正后实机尚待。原109末笔仍未知，真实retire独立记账，新局与旧证据隔离。


### 111加入关闭后变页补充

111(formal02ae642d09134c38bf1bb3dbfc39240f/native9f7abd42f31d4cda8de3ac585de3ab02)已真实通过原3秒开局1059/1075/1351姓名→专用关闭→地图，并命中两场战斗、四页结算、六次owned安全点及角色/羁绊奖励读回。第二次加入关闭后首次仍见joined，额外12秒姓名循环与实际地图变页竞争而停止character_readback_unknown；隔离120条账均终态/计数0，现场保存。共享_action_transition_readback的可选下一批比较改用原生Read单次1500ms节点，失败只返回原30秒页面等待；必须加入身份读回/阈值/输入/Stop规则未改。109加入帧→111实际地图帧零输入回放及共享开局/奖励/事件/商店/最终结算当前120项去重通过。尚无此最新修正的新实机证明，不重发111动作。保留97160 SDK seq9及109原未知/全部账；必要分支完成后仍须重新冻结隔离同批最后两局，再R38完整/精确37→38交付。证据E/isolation-after109/RUN111_REVIEW.json、RUN111_OPENING_LIVE_RECEIPT.json、JOINED_PROBE111_RECEIPT.json。

固定上游EntryRecognitionSession.kt:766–777记录异步关闭后旧加入帧可残留，4933以实际页面分派关闭，6056–6064连续加入批次返回页面识别。本地复用现有Read节点单次读取，失败本身不认定变页，只有下一次真实Scene确认地图/奖励才返回。相同名字仍非新加入，不同实际名字才进入下一批确认；date_changed/disconnected/character_input_unknown不吞掉。必须accept_joined读回及真实库存核对完全保持。


### 112共享纯读与列表终点补充

112已结束，formal543d76b1ea1a4c6491a8a259e648e2c0/nativead924cd85bea439391a0efd97bddb348，finished2026-10-07T09:32:13.618153+00:00，character_list_edge_unknown，计数0。隔离432条账全部真实终态；原109仍10801条原字节及末笔in_flight，未追补。112已实机通过原3秒开局、五场战斗、EX扫描前段、连续结算/角色/羁绊/遗物奖励及多次owned安全点；这些局部命中不能代表商店、Boss或最终结算完成。根因是有效角色列表回弹改变卡框/高亮和细微滚动位置，整页哈希无法辨识终点。五处终点调用统一角色身份/顺序、原滚动条.02容差；原签名诊断、24次上限、阈值和时间预算不变。三处可选同页下一批姓名比较共用单次原生Read；纯识别缺失回到原页面等待，Runtime未知/超时仍传播，必须加入确认不变。当前去重213项有效回放/检查通过，含109加入→111地图和112实际帧；旧替身缺少shared API的失败单独修正验证，不计失败XML全通过。六个功能文件之外SHA不变、指定函数之外AST相等。原用户选定两处上游行为继续；最新六文件尚待新实机。97160 SDK seq10 idle/内存保留。必要实机后恢复真实快照、冻结，同SDK同批started/cleared/cleared/finished计数1、2，再R38完整及精确37→38安装/两个桌面ZIP。证据E/isolation-after109/RUN112_REVIEW.json、SHARED_READ_MOTION112_RECEIPT.json。

上游依据：固定EntryRecognitionSession.kt:766–777、4933、6056–6064保留异步关闭后页面优先识别；vision/LabyrinthBattleTeamRecognition.kt:87–120、294的结构/未知身份观测。实际112的26个原生观察与最终保存帧为本次列表回弹根因依据，不将本地端点实现冒充上游原函数。三纯读后继由MaaCharacters.probe，五列表调用由CharacterNavigation.unchanged共享，没有新增动作或资格条件。商店和最终结算当前仅回放证明，下一局必须实际命中才升级级别。


### 113共享滚动读回与完整搜索恢复（当前修正）

固定上游 `LabyrinthBattleRosterSearch.kt:61-178/203-301/321`、`LabyrinthEntryRecognitionSession.kt:3302-3306/5557-5586/5744`：已执行滚动才能开始读回；完整搜索/筛选耗尽后临时排除找不到角色再规划。当前 `CharacterNavigation.move` 在真ACK后用原12秒纯读及既有滑条.02；`seek`完整属性页→ALL后才抛typed not-found；普通/Boss只缩本次规划池。上游筛选按钮缺失也可能排除，本地保持当前真实识别失败语义，不把按钮缺失、预算耗尽、unknown或not_sent伪作完整遍历。

| 共享调用/后继 | 已接线 | 回放证据 | 实机证据/真实缺口 |
| --- | --- | --- | --- |
| 开局候选→实际选人→邀请→加入 | opening.invite→seek/move，原姓名确认/3秒不变 | 182中native邀请2；原110源页handoff检查、111-113原开局实机证据复用 | 新滚动settle实机待验；旧开局局部成功不代表完整局 |
| 自由选1/2→邀请→加入，事件11072/33151/33152与选择印记 | rewards.choose_free→move，同一所属事件/商店后继 | 120调用者中的free exhausted/1或2/错加入/Stop；原Scene及108/109保存帧证据保持 | 职阶/选择印记新实机仍待验 |
| 普通/EX/Boss找人→队伍→开战 | _edit→seek；prepare/prepare_boss临时候选重规划；Boss先释放跨tab旧成员再添加 | 14导航(含113实际观察语义重放)、Maa原生组队3、正常/Boss重规划及未知边界 | 114 EX30502已有导航/组队/获胜实机；116 EX扫描完成后去选碰撞失败、修正待验；Boss重规划仍缺 |
| 普通战首尝试保留当前满队 | actual normal_challenge→hold_current；真实5槽全部可信且属于owned；有配置plan、最终读回和StartOnce验证 | team113-hold-callers40、hold-boundaries3；EX、重试、短队、unknown、名册外、无plan及final变化反例 | 114及116的20301普通首尝试已实际保留可信满5人；EX/Boss/重试/短队/unknown/名册外等反例仍为回放，不扩大为完整局通过 |
| EX/Boss有效效果列表/空列表→ALL→评分 | scan_effective→edge/move共享修正，actual IDs传Context | 原upstream_combat完整/空列表及当前调用者120；无需重改matcher | 114有效列表6次滚动→ALL→开战获胜；116有效列表17次滚动→ALL实际完成但后续组队失败；空列表/Boss仍缺 |
| 全名册扫描 | MaaRoster.scan→edge/move共用 | 通用导航14；旧roster测试只验证legacy，不冒称新scan | 当前无生产调用者，不能记运行接通 |
| 奖励批次、所属召唤、事件替选、商店刷新、最终结算 | 复用原Scene/ACK/业务readback；此次没有功能改动 | 原590/108109保存帧、111probe/112有效证据按未变文件及AST复用；当前120中关联控制项 | 111-113奖励/事件局部通过；商店新增链和最终完整通关仍缺，保持原清单未验标记 |

当前182项只证明回放和传输；证据 `E/isolation-after109/SHARED_NAVIGATION113_REVIEW.json`。两个旧/新账SHA均保持；下一次真实撤退113和独立新批后再升级实机状态。既有100次/30分钟为刷取约束，不误称全局通关30分钟预算。最终源码冻结后的同批两局及R38安装/交付未完成。


### 114共享报价与当前实机覆盖

| 项目/全部调用者 | 已接线及上游依据 | 回放通过 | 实机通过 | 真实缺口 |
| --- | --- | --- | --- | --- |
| 刷新提案/金额/购买/回货架 | EntryRecognitionSession:6920–7002；MaaShop._refresh→confirmation_terms→fresh ShopRecognition→余额读回 | refresh114-affected.xml：真实114帧与模拟后继，独立审查通过 | 114只到提案ACK+报价画面 | 修正后真实扣300、新货架和续购待验 |
| 普通遗物及随机/选择印记购买、完成/关闭 | MaaShop.run/purchase_once/purchase_readback/ShopRecognition共用confirmation_terms；原inline保持，新required row复用同帧行语义 | 旧54/72/81等受影响边界重跑通过，原imprint后继检查保持 | 114三笔1350遗物购买、完成、关闭与货架读回 | 随机和选择印记完整真实加入仍缺 |
| 开局、所属动画、奖励、普通满队 | 原113 shared navigation/hold及108109/110111共享修复未变 | 原182及未变共享证据复用 | 114开局、6战/12结算、20所属动画、普通20301完整队保留 | 多轮奖励特定批次、职阶邀请、最终动画仍按各项缺口 |
| EX实际详情/有效效果/列表导航/评分/开战 | encounters.observe_ex/scan_effective→Context→MaaTeam；固定14EX策略 | 原combat与182检查未受报价改动影响 | 114 EX30502及有效列表6次滚动→ALL→组队→真实开战和获胜 | 空列表、新Boss策略及其实际重试分支仍缺 |

首次red用例有测试目录未建错误，保留refresh114-red；修正目录后refresh114-red2在产品旧parser准确失败。中间green为11通过/1失败（新测试误期待raw _input异常类型）；只按原生产合同纠正测试，最终以refresh114-affected.xml的97通过为准。未调整原输入语义；不把未送出和结果未知混淆。原114账与画面/原生日志全部保持，不补写刷新购买。


### 115商品身份共享修正（已接线、回放通过，实机待验）

依据固定上游ShopRecognition:135–203/306–326、EntryRecognitionSession:2795–2810/5199–5248：选择与确认须关联同一遗物，实际购买完成页后提交。115在旧原文name contains失败；货架与确认的2850、弱体/削弱+3均相符，不能将其归因于114刷新报价修正，也不能仅以金额与系列替代物品身份。

| 全部相关入口及后继 | 当前接线 | 回放证据 | 实机与真实缺口 |
| --- | --- | --- | --- |
| 货架提案ACK→确认报价 | 原source名称/价格fresh guard同帧捕获当次遗物图标；复用参数化RelicIconCapture及原.85 Maa TemplateMatch，固定上游图标布局 | 上游真实货架/确认配对；115未修改确认保存帧与明确模拟货架图标，错误图标反例 | 115原提案ACK已通过；本次新图标关联尚无设备实机 |
| run→pending quote→purchase_once→最终输入guard | 仅实际图标匹配成功记录native_observed_icon；绑定quote的身份方式贯穿前读回及最终fresh帧。原精确OCR支路保持；不硬编码“退→诅”，不模糊名称 | 115实际确认原生读回/单次SavedImages确认；同名同价但最后图标变更零点击；完整模拟shop loop | 修正后真实购买待验 |
| 真购买ACK→独立完成页→关闭 | 只对当前图标已确认的quote延续身份；完成页仍必须真实complete/实际币种金额/购买了。无图标完成页不硬套图标识别；原有name=None、price输入分支关闭 | 完成金额/语句反例、ACK不等于inventory；完整模拟报价/ACK/完成/关闭/返回 | 115仅第一笔2100原路径完整；本次新身份完整实机待验 |
| 随机/选择印记及刷新 | 每次新proposal/refresh清旧模板；印记只复用现有精确职能及随机/选择语义，不借遗物图标。刷新原300报价/独立减300保持 | 模板清理/类型反例；114原帧、刷新/印记既有受影响检查 | 刷新实际扣费/续购、印记实际加入仍待 |
| 原三选一遗物→获得道具 | RelicIconCapture只增加可选布局/节点参数，默认原路径保持 | identity115-affected中的原图标8项有效复用；其余函数AST不变 | 不把商店新参数宣称为所有奖励实机重验 |

当前identity115-current.xml 109项通过，另8项未变原遗物图标检查，共117项；首次115检查115项通过后，独立审查发现最终fresh身份未传，反例identity115-fresh-red2复现后补参数并重跑109。首次新测试误将无readback的Batch.purchase期待为返回ACK，已按原合同保留purchase_result_unknown，未修改生产事务语义。当前覆盖仅离线回放，原1266条账、115现场与109旧未知全部保留。证明见E/isolation-after109/SHARED_IDENTITY115_REVIEW.json。

### 116候选身份碰撞与独立成员操作（已接线、回放通过，修正后实机待验）

固定上游 LabyrinthBattleTeamSelectionPlan.kt:195–200/650–671 以实际成员条去选，并对候选按ID分组。本地保持冲突目标不可选：所有相同ID碰撞候选清身份/trusted但保留原candidates、置信分数、位置；不以预期名册或较高分猜哪张是真的。原阈值、输入节点/超时、ACK/业务读回和成员重复拒绝保持。

| 共享入口/后继 | 已接线及验证 | 实机与真实缺口 |
| --- | --- | --- |
| CharacterRecognizer→MaaCharacters→MaaTeam普通/EX/Boss编辑 | 唯一recognize函数改动；116原样末帧两次读回得到4可信members，两1051候选Unknown；原生SavedImages1807只点击实际成员[274,606]，1051零点击且NativeInputNotSent | 116修正前去选1059成功、1807未发送；新修正仅回放，不冒称后继读回或实机成功 |
| opening/自由选人/事件/选择印记 | 候选统一降级后重新计算selected；opening缓存不能补回冲突身份；Toggle/Invite仍拒selected Unknown。新opening两拒绝边界及旧原生邀请/受影响奖励调用者通过 | 既有开局/奖励实机复用；新冲突分支、自由邀请及商店印记完整后继仍待实机 |
| seek/有效效果/名册 | seek、free及effective只收trusted，完整库存扫描继续拒Unknown；碰撞保留实际卡，不能变成“明确空列表” | 116 EX20501的17次滚动→ALL完成，非本次故障；空列表/Boss仍缺 |
| 选择与加入业务读回 | battle/boss实际members重复仍抛错；free原有count+selected子集可证明阶段选择，但Unknown仍阻止下一Toggle/Invite，库存必须真实joined IDs完全一致 | 不把输入ACK、selection_verified或旧not_sent补成实际加入 |

collision116-affected.xml的12模块180通过、collision116-final.xml的6通过，共186项无跳过。最初旧代码red和裸owner测试合同误用的失败XML保留，不计通过；定稿原生用例复用实际NativeTimings输入证据观察器。历史计数不重复累加；114/115商店源未改，已有有效证据按范围复用。独立审查config_wiring通过，AST除recognize和Counter导入外保持，启动1061范围中仅此功能文件改变。

116原局完整保留，最后OIDaf499529a2d548eca3a59526ad71905c是原生确证not_sent、1462条隔离账原字节不改；109原in_flight也不追补。原3战6页结算、9所属动画、3角色/3羁绊/2遗物及普通20301满队保留属修正前局部实机，商店未到；仍须新必要实机及最终冻结同批两局。证据 E/isolation-after109/RUN116_REVIEW.json、RUN116_ENDING_RECOGNITION_FAILURE.json、RUN116_PRE_GUARD_OBSERVATIONS.json、SHARED_CHARACTER_COLLISION116_REVIEW.json。


## 114报价共享后继重核及117开局前收口

| 范围 | 当前接线与依据 | 有效验证 | 实机缺口 |
| --- | --- | --- | --- |
| 遗物/随机/选择印记报价 | confirmation_terms共享所需同行金额；115当次原生图标/exact印记语义贯穿fresh guard、购买完成和专用关闭 | 当前shop SHA770845f3、relics SHAd8b043ef；identity115-current.xml109项、8原图标项沿用 | 新图标购买完整后继；印记邀请/加入及回货架 |
| 刷新 | 固定上游EntryRecognitionSession6920–7002；_refresh提案ACK→所需300→新确认ACK→实际余额减300→新货架→run续购，清旧商品模板 | 114实际帧及fresh guard，正/误余额后继；旧97项及当前109中的14专项沿用，不重复计数 | 真实扣300、新货架实际续购尚未命中 |
| 117成员ACK读回 | 固定上游BattleTeamRecognition287–317以真实成员条刷新队伍；本地复用原member_slots/match/refine，纯成员范围不充当候选列表/空有效列表，ADD前full读取 | member117-resumed94/95通过，唯一失败为测试错算原refine次数；member117-final16/16通过，合计98去重当前通过。保存帧native4.75秒/原12秒 | 新修正实机待验，未追补117业务成功 |
| 开局冻结 | 下一新隔离局先登记1061功能/资源输入和入口SHA，运行期间保持；结束按事实分新增缺口/已验证路径回归/无法归类 | 原109与117账字节、失败现场和当前SDK保留 | 下一局不计最终同批两局；最终冻结/交付仍待完成 |


### 独立修复合并后四类共享路径验收（120运行中）

| 项目 | 合并入口与后继 | 已接线／回放证据 | 实机与剩余缺口 |
|---|---|---|---|
| 最终关闭确证未发送恢复 | autoplay._do→_observe(drive_final=False)→业务后继→公共阶段更新 | 独立13项与合并531通过；not_sent真实账，零额外动画输入；未知结果不重发 | 新最终关闭/动画实机待；最终冻结同批两局待 |
| 连结关闭后的旧结果页 | links.choose.closed_reward_signature→choose_reward.item_scene(relic)→不同正文/实际变页 | 既有标准化结果签名与原20秒读回；531含链接连续结果及旧正文反例通过 | 120连结关闭验收中；实际新正文及无重复通用关闭须从日志登记 |
| 连续小滚动与列表边界 | CharacterNavigation.edge/seek、scan_effective、roster.scan、choose_free→本段锚点／明确两次稳定 | 13项累计微小滚动回归通过，531包含成员117/有效列表/完整列表/自由邀请；原.02、24/48、原budget保持 | 新累计滚动实机验收中；未遇完整自由选择/空有效列表不算实机通过 |
| GUI目标计数文案 | PcrLabyrinthOwner.ProgressCount＋目标完成正文 | Release重建0错误/12既有警告；最终Core程序集attempts/clears实际反射1/2、2/2中性文案；运行依赖版本原镜像一致 | 源码/构建通过；当前42项隔离实际控件编辑保存重开通过，人工窗口未验 |

本轮来源：独立PATCH固定SHA9e1884da40e7d35ebcc1d6fd8df9094f856317599cf05d01fef02bd98f13037e；encounters仅scan_effective合并，118构造器AST保持；117成员代码保持。D/S功能一致与LABYRINTH_SOURCES哈希已核。原全量1879通过/12失败/305跳过保留，10项资料夹具定点与2项业务复核分别复用；Windows标准库临时目录清理异常不做业务改动。合并531/公开副本16通过1私有帧跳过证据见E/isolation-after109/INDEPENDENT_MERGED_CHECKS.json。


### 120共享动作所需锚点与确认后继

| 范围 | 依据／入口 | 已接线与回放 | 实机和剩余 |
|---|---|---|---|
| 原生动作限定探测的控制读回 | 固定上游EntrySession商店关闭确认后地图；本地SceneRecognition.analyze→perform_scene_action→现有And动作 | 只转发原有control.anchor并同帧原生重读，阈值/3秒/guard不变；12类动作和120保存帧共13通过，25组受影响512通过 | 修正后实机待；120旧确认not_sent保持，不恢复旧操作 |
| 连续两个EX | MaaRunActions.observe_encounter→MaaEncounter资源缓存→scan_effective→MaaTeam | 118生命周期和新累计列表回放复用；Scene影响由512覆盖 | 120两EX各6滚动→ALL→组队→胜利及奖励实机通过；空列表/其余特定机制仍按清单未验 |
| 商店刷新/遗物购买 | MaaShop.run报价→购买→完成关闭→新货架 | 114/115未改代码与当前512验证 | 119/120真实刷新与续购通过；120五遗物后余额720，印记900未买，加入后继不填通过；离店确认尚未发 |

这是既有场景限定探测的控制依赖遗漏，独立四类补丁未改scene.py；无证据将其判为独立修复回归。未知旧动作不重发，120全部原账、原任务、保存帧保留。后续正常局期间继续冻结功能和资源；最终同SDK同批两局及R38两个ZIP仍待。


### 当前GUI实际控件编辑保存重开（121运行期间隔离验收）

19项均通过实际Avalonia控件的TwoWay编辑→真实保存按钮命令→新PcrLabyrinthSettings读回快照一致；13项文本控件在真实TaskQueueViewModel重新绑定后显示保存值，五个公会候选控件逐一编辑读回。共42项通过，真实日常文件和游戏设置未改、控制器/Agent/游戏输入均为0。使用独立合并后当前Release的Core e6860e489206f56fa38adaa99a1304c77cc0a8e8af955f657d9ce29c24976edf及Desktop 918c90b29c33d97aa7a2ec7697523d63dbb9364e56920fb94638e8b263ef6ae6；1061冻结输入逐项无变化。属于实际控件无头验收，不冒称人工桌面窗口；旧配置/默认/校验/真实运行快照和决策证据按未变代码继续复用。原首次夹具类型错误和依赖旧副本失败日志保留，不作为业务失败或通过。证据E/isolation-after109/GUI_EDIT121_RECEIPT.json、gui-edit121-run3.log。


### 121商店确认修正及EX后继实机命中

121持续冻结1061输入，真实快照刷新false/Boss multi。三笔遗物2850/2100/1350购买完成关闭→shop_leave 8fe8987ec1e94b6eab01992f069b21b3→shop_leave_confirm 24afcdb7b34b41159884e6c968d3af6b（实际shop_exit_confirmation、native_ok与input_action_verified均true）→地图preview 06cf800b1c524db983360deb65b9dea0→Boss30701 challenge a8034cef6452439498a00f6d4b1c5789。120共享所需物理锚点修正已实机通过；本局未买随机/选择印记，119加入后继仍待实机。EX30501（实际ex_challenge）六次有效列表滚动→ALL→组队→开战→两页实际battle_victory→角色加入关闭→遗物关闭→地图通过。五批連結专属关闭的后续通用关闭均是新joined页，未重复关闭旧item页。Boss目前仍在推进，最终结算及最后同批两局未验，不升级整条链完成。证据E/isolation-after109/LIVE121_PARTIAL_HITS.json。
