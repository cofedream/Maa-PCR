# r25 对应源码与构建

以交付的 r24 源码 ZIP 为冻结基线，仅修改 PcrAlphaOwner.cs、PcrPlayerSettings.cs、schedule_quick.py、supported_daily.py 和两个 GUI 项目的版本字段；未合并当前工程其他实验改动。GUI 版本2.16.4、更新 revision25。

GUI 使用 .NET SDK10.0.101，Release/win-x64；相同第三方依赖沿用r24源码与notice。离线还原使用现有NuGet缓存：

```
dotnet restore gui/MFAAvalonia/MFAAvalonia.Desktop/MFAAvalonia.Desktop.csproj --configfile NuGet.Offline.Config -r win-x64 -p:Configuration=Release -p:NuGetAudit=false -p:DisableBeauty=true -p:PublishReadyToRun=false -p:SelfContained=false
dotnet build gui/MFAAvalonia/MFAAvalonia.Desktop/MFAAvalonia.Desktop.csproj --no-restore -c Release -r win-x64 -p:Platform=x64 -p:DisableBeauty=true -p:PublishReadyToRun=false -p:SelfContained=false
```

通过 scripts/create_pcr_apphost.proj 生成仅寻找 AppRelative runtime/dotnet 的便携 apphost；运行时沿用 r24，不将系统运行时回退作为交付依赖。Python两模块与运行包逐字节相同。delivery/runtime-package-manifest.json 是此源码对应的实际运行清单。

新增 tests/test_gui_adb_schedule.py 可独立验证无MuMu身份的新玩家配置与旧CLI保护。正式GUI测试使用同版Core、Avalonia Headless和官方CustomController，TCP Agent执行真实业务函数，替换设备为本地保存帧；私有游戏图片与日志不随源码分发。完整离线验证范围和外机实测缺口见 RELEASE_R25.md。
