# r24 对应源码与构建

## 范围与对应关系

本包以 r23 的 930 项冻结源码白名单为基础，叠加最终 GUI 白名单、稳定运行包全部 manifest-owned Python/脚本/Pipeline 源码、6 个业务修改文件和 2 个专用测试；54 项候选业务文件逐项匹配最终冻结，其中包括保持原始字节的 3 个 OCR 模型载荷和 README。GUI 为 MFAAvalonia 2.16.3，移除可选规划入口残留，使用本次原创图标、缺图占位、通知音和文件夹图形。业务识别不再引用被移除的 9 张游戏 PNG；Python 与 Maa Pipeline 的当前原文保留在 `src/`、`scripts/` 和 `assets/resource/pipeline/`。

`SOURCE_INVENTORY.json` 记录最终文件及输入冻结 SHA256。GUI build-03 与 publish-03 在 Windows x64、.NET SDK 10.0.101 上真实成功；前两次编译失败及修正经过保留在 `docs/GUI_ASSET_CHANGES.md`。原创资源的生成器和许可来源分别为 `scripts/generate_gui_release_assets.py` 与 `docs/licenses/gui/OriginalAssets-PROVENANCE.json`。本包没有复制本机缓存或 bin/obj，也没有把旧 r20 二进制说成此次源码的编译结果。

本次有效组件记录为 `docs/licenses/RELEASE_COMPONENTS-r24.json`。原 `docs/licenses/COMPONENTS.json` 和旧 release 文档保留为历史审计，其旧版本、pending 分类和旧资产记录不作为本次清单。新报告明确列出当前 15 项 GUI 编译输出、6 项业务修改、9 项删除、模型原始 SHA 与许可副本；本次最终运行包验证结论以维护者封存回执为准。

## GUI

从源码根目录运行以下公共等价命令。实际验证使用了仅指向本机已有包缓存的 NuGet 配置及全新恢复目录，该私人路径配置不随源码交付。联网恢复使用上游 NuGet 源；若使用离线恢复，自行提供合法取得的同版本包与配置。源码已固定 Packaging.Targets 0.1.232；其他版本引用以项目文件为准。

```powershell
$P = 'gui/MFAAvalonia/MFAAvalonia.Desktop/MFAAvalonia.Desktop.csproj'
dotnet restore $P --packages .build/packages --disable-parallel -r win-x64 -p:Configuration=Release -p:Platform=x64 -p:NuGetAudit=false -p:DisableBeauty=true -p:PublishReadyToRun=false -p:SelfContained=false --nologo
dotnet build $P --no-restore -c Release -r win-x64 -p:Platform=x64 -p:NuGetAudit=false -p:DisableBeauty=true -p:PublishReadyToRun=false -p:SelfContained=false --nologo -v:minimal
dotnet publish $P --no-restore --no-build -c Release -r win-x64 -o .build/publish -p:Platform=x64 -p:NuGetAudit=false -p:DisableBeauty=true -p:PublishReadyToRun=false -p:SelfContained=false --nologo
```

便携运行包的入口另外使用 `scripts/create_pcr_apphost.proj` 生成，只查找旁边 `runtime/dotnet`，不回退到系统运行时。SDK 与 HostTemplate 使用验证版本，不将本机绝对路径写进源码。下面将 `$SDK_ROOT` 设置为自行安装的 SDK 根目录：

```powershell
dotnet msbuild scripts/create_pcr_apphost.proj /t:CreateEntry /nologo /v:minimal "/p:SdkTaskAssembly=$SDK_ROOT/sdk/10.0.101/Sdks/Microsoft.NET.Sdk/tools/net10.0/Microsoft.NET.Build.Tasks.dll" "/p:HostTemplate=$SDK_ROOT/packs/Microsoft.NETCore.App.Host.win-x64/10.0.1/runtimes/win-x64/native/apphost.exe" "/p:EntryOutput=$PWD/.build/publish/MFAAvalonia.exe" "/p:GuiAssembly=$PWD/.build/publish/MFAAvalonia.dll"
```

编译输出还不是完整运行包，需要匹配的 Python、.NET、VC++ 运行时、Maa/native 载荷、受许可的 OCR 模型、目录布局和最终 manifest。维护组装器 `scripts/assemble_release.py` 要求指定已冻结的完整基线和 GUI/业务/许可材料；它不是从任意干净仓库一键重建所有第三方依赖的工具。`scripts/add_provenance.py` 是本次真实使用的有效组件记录加入 owned manifest 的独立步骤，不启动玩家程序或游戏。

`delivery/runtime-package-manifest.json` 是 root 封存的 r24 运行包 manifest 原文快照，只有程序文件 SHA、版本和工程分类，不含私人数据；原始 SHA256 为 `b0119e2cd7a8268dcf70742ed5f848418be641fc51867fe83024380c043f1cce`。源码 inventory 绑定这个原文，不反向把源码 ZIP 的 SHA 写入运行包 manifest，避免循环依赖。有效组件记录中的“封存待完成”是该记录生成时的状态，后续完成的封存由这个快照证明。

## Python 与 Pipeline

项目要求 Python 3.10+，此次内置运行时为 CPython 3.11.16，MaaFramework Python 包固定 5.13.0。`requirements.txt` 与 `pyproject.toml` 是当前源码依赖声明；未固定的 NumPy/OpenCV/Pillow 版本不能被误称为此次冻结运行环境。可在独立开发环境安装依赖，先做无设备检查和专用回归：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -X utf8 -B scripts/pcr.py --help
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_pixel_free_resources.py tests/test_pixel_free_history.py
```

测试和帮助不会代替实机执行验证。此源码包不含游戏图像模板、私人截图或玩家数据；`assets/resource/model/ocr/` 的 3 个 OCR 载荷和 README 按稳定 manifest 原始字节保留，来源与许可见该 README 和第三方材料。开发者应按对应组件的许可和路径使用真实资源，不能伪造占位模型，不能把 `doctor` 或业务启动当成无设备命令。玩家通过完整运行包使用，不需要上述开发步骤。

## 第三方对应源码

`third_party/materials/MATERIALS.json` 的 `source_delivery_whitelist` 精确列出 9 个原始源码归档，`notices` 列出 106 份原始许可/notice，路径相对 `third_party/materials/` 保持不变。包括 MaaFramework 5.13.0、MaaUtils、.NET Maa binding 5.10.0、MaaAgentBinary 固定版本、MaaTouch、minitouch、minicap、android-libjpeg-turbo 和 libevdev。解压各归档后，按原上游 README/CMake/Gradle 配置构建；MaaFramework 的 MaaUtils 子模块使用 `MATERIALS.json` 中固定提交的独立归档补齐，不任意取最新版本。

MaaFramework 的 MuMu/LD 封装代码是主框架源码的一部分，动态加载玩家自行安装的模拟器 SDK DLL。运行包没有 `external_renderer_ipc.dll` 或 `ldopengl64.dll`；对应专有接口头文件未随本源码包复制。重建这些封装时，须独立获取相关模拟器 SDK 接口并遵守厂商条款。参考的 EmulatorExtras 固定版本及 MaaDeps v2.12.6 外部构建依赖取得方式见 `third_party/materials/HANDOFF.md`；不对无授权 SDK 头文件或 MaaDeps 自身构建脚本主张本项目许可。

保留固定源码、原始版权、发布产物字节一致和实际 GUI 编译成功，是不同类型的证据。本包不声称重新执行了所有第三方历史工具链或完成确定性逐字节重建。18 个 Android29-31 minicap 文件已与固定 MaaAgentBinary 仓库一致，但其历史完整构建关联未证明；这项证据局限不应被写成虚构的重建证明或自动的法律禁止。

## 运行时许可保留

完整运行包必须保留 `runtime/python/LICENSE.txt`，所选 site-packages 的原始许可/版权及 dist-info 元数据，以及 `runtime/dotnet/LICENSE.txt`、`runtime/dotnet/ThirdPartyNotices.txt`。Python 主 LICENSE 原始 SHA256 为 `886a0ead2d89030ee62dbff52b04e47ab91998341295bb9c56fb952b4e081c7a`；.NET 两份原文的 SHA256 分别为 `7f6839a61ce892b79c6549e2dc5a81fdbd240a0b260f8881216b45b7fda8b45d`、`deb4427a295e1ed474b0d81c5a0d972c1b550b9a715cda939cdfa9236b1b418f`。

本源码包也在 `docs/licenses/runtime/` 保留上述运行时及所选 Python 包的原始许可副本，每份的 SHA256 和 stable manifest 来源列入 SOURCE_INVENTORY；不复制完整运行时对象码。运行包自身必须继续保留原路径的原文和元数据，不能用源码包副本替代。

此次内置 .NET 10.0.1 原始 LICENSE 是 MICROSOFT SOFTWARE LICENSE TERMS / MICROSOFT .NET LIBRARY，不是 MIT，包含对象码再分发条件。项目源码、独立第三方库和运行时分别按自己的原始条款交付；不得将整个运行包改标为统一 GPL/MIT。这里是材料与工程范围说明，不是正式法律结论。
