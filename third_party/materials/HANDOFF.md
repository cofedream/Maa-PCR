# Pinned Dependency Materials

This work only reads the installed stable r23 manifest-owned program files.
It does not change the GUI, pipelines, Python program, settings, or game state.

## Integration

1. Add `delivery-notices/` intact to the player license materials and matching
   source companion. Original notice bytes and archive entry provenance are
   listed in `MATERIALS.json` under `notices`.
2. Add only the nine archive paths under `source_delivery_whitelist` to the
   matching source companion. Preserve the original archives or their exact
   original source bytes, original notices, and fixed commit identities.
3. Include the matching current modified GUI/Python/pipeline source and build
   files from the release freeze prepared by the other owners. The dependency
   archives do not substitute for this project's changed source.
4. Retain a source/build guide explaining the external SDK and build dependency
   retrieval below. Do not claim that these byte-origin checks are an
   independently reproduced deterministic build.
5. Do not select material under `not_for_delivery`. In particular, do not copy
   proprietary EmulatorExtras SDK headers, unrelated STF tools, full dependency
   wheels, or the MaaDeps developer binary tarball into the player package.

## Fixed Sources

- MaaFramework 5.13.0 release: commit
  `2bcfa85c66a2eac6ca3e5937f175495275ee0643`, LGPL project source plus Python
  binding source, CMake files, packaging and build instructions.
- Required MaaUtils submodule: commit
  `6e9ba33f6ad835418097d9324c01c44a82825a2b`. Its source archive and original
  license material are included.
- .NET Maa binding 5.10.0: original cached NuGet metadata pins commit
  `27c69a5b8ff41b6002ead71f403a16f442a7168e`. The original archive includes
  both binding implementations, package configuration, and LGPL license.
- MaaAgentBinary GUI 1.2.0 and Python 1.0.1: both packaged data trees exactly
  match the repository at `bd83c0fdd8c33040a7845f2bf6801e532842ee39`.
  The fixed repository data archive and original project AGPL notice are kept.
  Individual upstream components have their own original licenses, not a
  replacement blanket license.
- MaaTouch: shipped bytes match the official v1.1.0 release artifact exactly;
  associated source commit is `a1e003ccef0fd039b9ae7263179e3de74ce654e4`.
  Original Apache-2.0 license and full Gradle source archive are retained.
- STF minitouch 1.2.0: annotated source tag resolves to
  `9e742e964465b67a800f371aee27e940726577a0`. Vendor libevdev is pinned at
  `d447a75e9e23f2286c44859f07b955981e82d483`; full source and original COPYING
  are retained. Its original COPYING distinguishes the Linux-header notice
  from linked library code.
- STF minicap 2.3.0: annotated source tag resolves to
  `3686d9d4abe847195ef76d1753d02d11deed424f`. Vendor android-libjpeg-turbo is
  pinned at `46be77d8b8287ea3687da6ab245032929363515b`; original source and
  license material are retained.

## External Build Dependencies

The main MaaFramework source archive contains empty submodule placeholders,
not the proprietary SDK interface files. Its MuMu/LD LGPL wrapper sources
include vendor interface declarations and dynamically load the user-installed
`external_renderer_ipc.dll` or `ldopengl64.dll`. Neither vendor DLL is in the
installed owned payload inventory. Rebuilding the wrappers requires obtaining
the external emulator SDK interfaces separately, subject to the vendor terms.
The reference submodule revision is
`54d3a3ad448f0541df3759ae91b571a6762daaf1` at
https://github.com/MaaXYZ/EmulatorExtras; its README states No License and
reserves the respective vendors' rights. No vendor SDK rights are asserted.

MaaFramework's original `tools/maadeps-download.py` selects MaaDeps v2.12.6,
commit `884887968f8995f8217b28b2a470ec3620a9c660`, at
https://github.com/MaaXYZ/MaaDeps. This is an external build tool/dependency
reference. No MaaDeps project license text was found in its archive, so the
archive is audit-only rather than silently licensed by this project.
Original vendor copyright files from that exact official Windows developer
attachment are retained separately. Five distributed native vendor DLLs match
its original DLL bytes exactly.

Optional quickjs submodule and the testing data set are not selected. Their
fixed revision references remain visible in the upstream repository/build
files; the stable Python/.NET player does not ship a QuickJS binding.

## Evidence Scope

The report distinguishes original published wheel identity, original NuGet
repository declarations, fixed source bytes, official release binary matches,
vendor notices, and external SDK retrieval. It does not claim that every
historical toolchain run has been reproduced. Eighteen Android29-31 minicap
payloads match the pinned MaaAgentBinary repository but not the README-linked
STF mirror; their exact historical build association is not proven. This fact
must not be changed into either an invented rebuild proof or an invented
blanket prohibition under the upstream Apache license.

Game template and GUI artwork rights are outside this task and have separate
owners. Final integration/privacy/launch tests and final release classification
remain the parent agent's responsibility.
