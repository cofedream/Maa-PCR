# R30 构建说明

GUI、内置运行库和模型沿用R29；GUI构建步骤继承SOURCE_BUILD_R29.md及其固定版本依赖和许可证。R30只改rewards.py、supported_daily.py、rewards.json并新增版本说明。当前运行清单及对应源文件SHA见delivery/runtime-package-manifest.json和docs/licenses/RELEASE_COMPONENTS-r30.json。

使用现有正式补丁构建器，以精确R29安装作--base，独立R30候选作--target，产品/布局/repository绑定不变。更新须退出软件和Agent，保留玩家数据。

本轮测试15项体力暂缓专项及相关回归最终289通过、2个历史私有帧用例跳过；首次TCP用例缺隐藏测试helper，仅补齐测试环境后单项通过，原失败保留。新专项原图需维护者提供，源包不含私人截图；不宣称源包缺样本情况下能复现原图测试。未实机操作。
