# 采用与排除

只读参考目录为父端提供的 `pcr-rebuild-20260922`，未联网，未导入旧工程。以下 commit 为父端核准值；本轮不重复宣称独立核准。

| 来源 / 版本 | 本轮使用 | 许可 |
| --- | --- | --- |
| MaaFramework v5.13.0 | 采用已装官方 Resource/Tasker/AdbController/CustomController；按 `docs/zh_cn/3.1-任务流水线协议.md`、`3.3-ProjectInterfaceV2协议.md` 和 Python binding 写新代码。只读核对 `source/MaaAdbControlUnit`，未执行 demo1 的设备示例 | LGPL-3.0；原文保存在 docs/licenses/MaaFramework-LICENSE.md |
| MaaPCR ba4d5c23300163bb5719f149b7291cfcd93389f4 | 仅参考 `assets/resource/pipeline/masterstroke_Sweep_task.json` 的入口→预设职责；不复制 JSON、日服图片、泛化 OK、结算/消费节点或 day_task | GPL-3.0 |
| Rino 0b2586642c9965707ef789d07579e49550d47228 | 仅参考 `Resource/base/pipeline/masterstroke_Sweep_task.json`；核对 day_task 含 mana 购买，不采用日程表整包、day_activities_task、繁中图或自动器/UI | 新增/修改 AGPL-3.0，继承 MFAWPF 部分 GPL-3.0；已读 LICENSE 与 NOTICE，无代码/素材复制 |
| PCF dc37848d2437ce66e92d9e82186beae13d679c08 | 仅参考 `scenes/fight/fightinfo_base.py`、`auto_advance_settings.py`、`buy_ap.py` 的国服体力/购买分支；不采用 Python 状态机、UIAutomator、凭据或任何点击实现 | GPL-3.0 |
| MaaCommonAssets dabcd4681ac990dc4361de26416d986abd80e4aa | 使用父端已复制的 `OCR/ppocr_v4/zh_cn` 模型及原 README；不修改模型 | 仓库 MIT；PaddleOCR 模型来源/链接保留原 README，PaddleOCR Apache-2.0，未重新下载验证模型授权链 |
| 父端 live 国服截图 | 复制七张私有回放证据至 tests/fixtures；从主页/冒险/主线三张裁剪六个局部稳定标签模板。脚本 `scripts/prepare_fixtures.py` 记录坐标，不覆盖已有文件 | 游戏图像权利归原权利人；只作本地私有验证，不作为开源素材授权或公开上传 |

本项目 Python 与 Pipeline 为新实现；没有复制上述 GPL/AGPL 自动流程。许可证原文副本只是保留依赖说明，不能用于宣称拥有游戏图像权利。

按槽位扫描新增私有回归原图 09..14，来源和 SHA-256 见 `tests/fixtures/preset-scan-sources.json`。12/13 是本账号五条已勾选关卡的顶／底回归，10/11 是全部关卡反例，09 是搜索结果短列表，14 保留父端恢复显示证据。它们均已参与开发，不称独立留出，不将五条名称或数量写进生产终止条件。

`scan_check.png` 仅裁剪 12 图在 Maa720 坐标 [1106,244,59,55] 的勾选图标；逐行以相对 OCR 位置进行原生 TemplateMatch。滚动条和槽位采用原生 ColorMatch，文字采用原生 OCR。`scan_no_rail.png` 是 09 图 [1216,237,15,261] 的私有局部诊断裁剪，保留其创建现场；生产使用全区域颜色验证，避免恒色模板的归一化相关性误命中。两张裁剪同样没有开源游戏素材授权，不应随公开分发。

合成回归只在内存中改视图标签、槽位颜色／名称、遮挡区域、次数或标题；回执 source 明确区分 synthetic。Python 自定义 recognition 调用 Maa 原生识别并按 OCR 框组成行，自定义 action 只合并字段／判定数据缺口；没有 Python click、swipe 或业务循环。Maa Pipeline 负责切页、重试、滚动上限和停止。

## OCR 精确来源核对（2026-10-01）

本地只读核对已证明 det.onnx、rec.onnx 与 MaaCommonAssets 提交 `dabcd4681ac990dc4361de26416d986abd80e4aa` 的 `OCR/ppocr_v4/zh_cn` Git blob 全字节一致；keys.txt 与 checkout 一致，和 Git blob 仅 CRLF/LF 不同。三个当前文件的 SHA-256 与对应关系保存在 `docs/licenses/COMPONENTS.json`，运行包中位置为 `licenses/project/COMPONENTS.json`。

该提交的 OCR/README 保留 paddle2onnx 导出及优化命令；没有找到具体原始 Paddle tar 哈希、转换工具版本或明确覆盖具体权重的授权材料。上述核对证明文件来源，不解除权重、游戏模板或玩家分发资格缺口。游戏模板的现有来源脚本和私有验证范围保持。
