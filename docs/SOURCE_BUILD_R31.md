# R31 构建说明

沿用 R30 的 GUI、原生库、OCR 模型和内置运行库及固定依赖材料。本次业务变更仅 src/pcr_maa/abyss_native.py、assets/resource/pipeline/abyss.json，另附版本说明。GUI 构建说明继承 SOURCE_BUILD_R29.md。

使用 scripts/build_pcr_player_patch.py，以精确 R30 安装作 --base、R31 候选作 --target。真实运行修订见运行清单，不以 GUI 组件版本判断。升级通过正式更新器，先退出程序及 Agent。测试所需私人原图由维护者另行提供；原失败帧测试支持 PCR_ABYSS_TICKET_EVIDENCE 指定 ticket.png、reading.json、quote.json 所在目录。
