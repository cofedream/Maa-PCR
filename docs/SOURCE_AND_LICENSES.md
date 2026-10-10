# 对应源码与第三方许可

向他人提供本版运行包时，同时提供本版匹配的源码包。玩家只需解压运行包；源码包用于查看、修改和构建，不用于启动程序。不要只给旧版源码或空白上游链接来代替本版实际修改源码。

## 许可边界

- 本项目原始 Python、pipeline 和工具代码使用根目录 `LICENSE` 的 MIT 许可。
- 修改后的 MFAAvalonia 界面遵守 GPL-3.0，原文在 `licenses/MFAAvalonia-LICENSE.txt`，对应源码在源码包 `gui/MFAAvalonia/`。
- MaaFramework 原生库和 C# binding 遵守 LGPL-3.0；MaaAgentBinary 项目与其内部各组件分别保留原许可，不能用一个许可证覆盖它们。
- 字体、OCR 模型、GUI 第三方库、Python 包和 native vendor 库保留原始许可证及版权信息。旧来源审计是历史材料；本版新增来源和 notice 以 `licenses/verified-r24/MATERIALS.json` 为准。
- 原创替换图标、缺图占位、通知音及文件夹矢量的作者、许可、生成代码和哈希保留在匹配 GUI 源码中。它们不是游戏截图或第三方图片的改色版本。

整个压缩包不统一重许可为 MIT 或 GPL。GPL/LGPL 源码交付和哈希记录不代表游戏官方授权。

## 附带运行时

CPython 的原始许可证汇编保留在 `runtime/python/LICENSE.txt`，第三方 Python 包的许可证和 metadata 在各自 site-packages 目录。

.NET 的原始条款和第三方声明保留在 `runtime/dotnet/LICENSE.txt`、`runtime/dotnet/ThirdPartyNotices.txt`。附带的 Microsoft .NET Library 二进制和 Microsoft Visual C++ 运行库按其原始条款使用和转发，不变更所有权、不得移除声明，不将 Microsoft 的运行时源码改授项目许可。转发附带的 Microsoft 运行时须继续保留原条款，并满足其对分发者及终端用户的适用要求。此说明不限制用户依据 GPL、LGPL 或 MIT 对其他独立组件享有的权利。

## 固定版本源码

源码包附带 MaaFramework 5.13.0、必要 MaaUtils、C# binding 5.10.0、固定 MaaAgentBinary、MaaTouch 及 minitouch/minicap 的源码归档和必要 vendor 源码，共 9 份。版本、commit、来源与每份哈希记录在 dependency materials 中。

未复制未授权的模拟器 SDK 头文件或原始 SDK DLL。MaaFramework 的 LGPL 包装代码保留，编译相关模拟器接口时需要按上游说明另行取得厂商 SDK 并遵守厂商条款；运行时动态使用玩家已经安装的模拟器组件。完整 MaaDeps 工具归档未重许可或附带，原生依赖版权原文单独保留。

源代码与实际固定版本的关联、第三方声明、独立 GUI 编译及文件映射均有记录，但不宣称历史二进制已逐字节重建。18 个历史 Android29-31 minicap 文件已映射到固定 MaaAgentBinary 载荷；其更早的构建工具链关联仍未证明。OCR 的历史转换工具版本与转换日志也未重现，这些局限保留在来源报告中。

## 游戏素材和使用风险

日常部分沿用有页面约束的文字、颜色和形状识别。黎明界集成新增上游识别模板、角色图像和社区数据，分别按 `docs/LABYRINTH_SOURCES.json`、`licenses/labyrinth/` 与各资源包的声明记录来源及许可；不将游戏图像授予项目许可证。维护者的实际日志、证据截图、账号设置和账本不进入交付包。

本软件非官方产品，不代表游戏权利人认可。是否使用账号自动化由玩家自行评估游戏规则和风险。源码开放不会自动赋予第三方图片、商标或游戏内容的权利。


## 黎明界移植

上游 `wbero/limingjie-assistant` 固定版本与逐文件关联见 `docs/LABYRINTH_SOURCES.json`。其移植代码保留 CC BY-NC-SA 4.0；协议客户端、Maa、角色数据与各资源保留各自条款，不统一改授 MIT。当前集成供本机候选验证，许可文本与来源记录不推定整体公共分发授权。
