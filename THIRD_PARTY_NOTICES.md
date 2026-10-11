# Third-Party Notices for R39 (v0.1.0 candidate)

The original project source license does not replace licenses of the modified
GUI, dependencies, fonts, models, or bundled runtimes. Retain all original
license and copyright files when conveying this package. Provide the matching
same-tag source ZIP alongside the runtime ZIP. R39 is a development candidate
until the acceptance gate passes; historical r24/r25/r26 records below retain
their original scope, not a new blanket licensing or distribution verdict.

| Component | Version and retained material |
| --- | --- |
| Modified MFAAvalonia and local GUI projects | GUI component 2.16.6; player release v0.1.0 / R39 is separate. GPL-3.0 GUI source and original notices accompany matching source. |
| Original GUI assets | The missing-image placeholder and synthetic chime retain the project MIT declaration; the original folder geometry is covered by its containing GUI's GPL-3.0 license. These three assets are recorded in `docs/licenses/gui/OriginalAssets-PROVENANCE.json`. |
| User-provided replacement logo | The current `logo.ico` is converted from the user's supplied image; input and output hashes are recorded in `docs/licenses/gui/UserProvidedLogo-PROVENANCE.json`. Original authorship and license are unestablished; the supplied image and converted icon are not declared original project artwork or MIT-licensed. |
| MaaFramework | Actual native/Python 5.13.0, fixed source commit `2bcfa85c66a2eac6ca3e5937f175495275ee0643`; LGPL original license and main/MaaUtils sources provided. |
| C# Maa bindings | 5.10.0, source commit `27c69a5b8ff41b6002ead71f403a16f442a7168e`; original LGPL license and source provided. Declared historical NuGet runtime versions are distinct from the actual native 5.13.0 overlay. |
| MaaAgentBinary | GUI 1.2.0 and Python 1.0.1 payloads map to `bd83c0fdd8c33040a7845f2bf6801e532842ee39`; original AGPL project text and pinned repository provided. Each nested component retains its own terms. |
| MaaTouch, minitouch, minicap | Fixed source/archive identities and original Apache-related notices in `licenses/verified-r24/`; source archives and necessary vendor source supplied. Exact deep historical build association for 18 minicap files is not asserted. |
| NETCore.Encrypt and SharpDX | NETCore.Encrypt 2.1.1, SharpDX/DXGI/Direct3D12 4.2.0: original pinned MIT copyright/license texts in `licenses/verified-r24/licenses/`. |
| Native dependencies | Original notices extracted from the fixed official MaaDeps v2.12.6 developer attachment, with five exact shipped DLL matches. Do not apply the project license to vendor code or to MaaDeps build tooling. See `MATERIALS.json`. |
| CPython and Python packages | R38 inherited Python 3.11.16; R39 clean-build runtime is fixed in the build dependency lock and actual `package-manifest.json`. Runtime license and distribution metadata must accompany the chosen payload; the versions are not interchangeable. |
| Microsoft .NET runtime | 10.0.1; original `runtime/dotnet/LICENSE.txt` and `ThirdPartyNotices.txt` retained. Microsoft library terms apply separately; the aggregate is not wholly MIT/GPL. |
| Microsoft Visual C++ runtime | Original x64 app-local redistributable files retained; fixed file mappings and applicable Microsoft Redist references in `licenses/project/COMPONENTS.json`. |
| Other GUI libraries | Version declarations, original licenses and historical package origins retained with matching source. Actual rebuilt native/runtime bytes are recorded by the R39 package manifest, not assumed unchanged from older releases. |
| SukiUI and fonts | Original SukiUI MIT, Roboto Apache and Noto Sans SC OFL texts remain under `licenses/project/gui/`. Font bytes are unchanged. |
| OCR models | Pinned MaaCommonAssets model identity, MIT repository text and PaddleOCR Apache material retained under `licenses/project/`; actual model bytes unchanged. Historical conversion reproducibility limitations remain documented. |

`licenses/verified-r24/MATERIALS.json` lists 106 added original notice files,
fixed sources and exact binary-origin checks. The source delivery guide explains
external emulator SDK retrieval; proprietary SDK headers and original SDK DLLs
are not included or relicensed.

Historical audits in `licenses/project/COMPONENTS.json` describe earlier local
candidates; their pending-review wording is not the r24 release verdict. New
source/artwork changes, actual tests and r24 checks are separately recorded.
They are not a claim that historical build outputs reproduce bit for bit.

The earlier daily-template removal does not describe all labyrinth assets.
R39 retains the labyrinth integration, including its upstream CC BY-NC-SA code,
GPL-derived catalog and game-image provenance in `docs/LABYRINTH_SOURCES.json`,
`licenses/labyrinth/` and `assets/labyrinth/character-pack/PROVENANCE.json`.
Original component terms and unresolved provenance statements are retained;
the root MIT license applies only to original project source, not this entire
aggregate. No maintainer settings, private game frames, journals, sessions or
logs are included. See `docs/SOURCE_AND_LICENSES.md` for component boundaries.


本次 r25 的有效源码与编译产物对应记录见 docs/licenses/RELEASE_COMPONENTS-r25.json。r24及更早材料保留为历史来源，第三方原许可保持。


r26有效组件与对应源码记录见 docs/licenses/RELEASE_COMPONENTS-r26.json。原依赖、资源、许可证沿用r25；旧记录保留历史范围。
