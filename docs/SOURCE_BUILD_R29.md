# R29 对应源码和构建

GUI沿用冻结R26的2.16.5，构建步骤沿用SOURCE_BUILD_R26.md与SOURCE_BUILD_R25.md：.NET SDK10.0.101，Release/win-x64，便携apphost和内置.NET10.0.1。GUI未重新改版。当前Python/原生资源则为R29；内置CPython3.11.16、MaaFramework5.13.0及其固定版本绑定，版本与全部载荷见delivery/runtime-package-manifest.json。

原始源码基线为已交付R26；本次同步冻结R27/R28/R29的对应业务变更，包括abyss_reconcile.py、preset_result_reconcile.py、result_checkpoint.py。运行包中src、scripts和pipeline按路径逐项与本源码核对。依赖固定版本源码归档和必要vendor源码保留，GPL/LGPL与其他原许可不变。

SOURCE_INVENTORY.json核对本次导出的实际文件；docs/licenses/RELEASE_COMPONENTS-r29.json核对运行包及当前业务映射。历史私有帧、真实配置和原始账本不作为公开源码材料，原图测试需另提供相关离线fixture，不能凭缺少fixture声称业务已实机通过。本次业务验收沿用已完成R29正式回归，不因归档重复全量。
