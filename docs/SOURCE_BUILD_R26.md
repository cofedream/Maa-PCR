# r26 对应源码与构建

以交付 r25 源码为冻结基线；只修改 PcrPlayerSettings、PcrMuMuDisplay、PcrAlphaOwner、TaskQueueView、RootViewModel 和两个 GUI 项目版本。GUI2.16.5、revision26。构建方法沿用 SOURCE_BUILD_R25.md 的 .NET SDK10.0.101、Release/win-x64 及便携 apphost 步骤；业务 Python、第三方依赖、OCR 模型未变化。当前主工程中的其它实验未导入。

新设置 validateDisplayMapping 为布尔值，旧设置未含此字段时默认false；不增加Python配置字段。默认模式仍核对游戏和输入在显示屏0，只跳过物理Viewport解析。严格模式及旧配置确认入口保留原检查。GUI横幅从程序集读取版本。

SOURCE_INVENTORY.json记录完整白名单源码；delivery/runtime-package-manifest.json 与运行包清单相同。实际 GUI 与官方 CustomController 的离线回归范围见 RELEASE_R26.md；私有游戏保存帧与日志不随源码分发。
