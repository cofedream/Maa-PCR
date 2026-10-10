# r24 GUI Asset Changes

Copyright (c) 2026 pcr-agent-maa contributors.

The new app icon, missing-image placeholder and notification chime are original,
code-generated assets released under the project MIT license (`LICENSE`).
`scripts/generate_gui_release_assets.py` contains their complete source and uses
only Python's standard library. It reads no old image, sound, font, trademark,
game frame or other input asset. Authored on 2026-10-03 by pcr-agent-maa
contributors with Codex-assisted original geometry and audio synthesis.
`docs/licenses/gui/OriginalAssets-PROVENANCE.json` records exact byte hashes.

The notification resource path and enabled/disabled setting are unchanged.
The window/tray icon and Markdown fallback image retain their existing paths,
types and public API. No game recognition template is replaced by these assets.

The previous Icons8-named file chooser image, SukiUI package icon and GUI preview
image are excluded from the new source and binary resource declarations.
The file chooser uses a newly authored simple folder `PathIcon` geometry at the
same 24x24 size and margin. This source change is covered by the containing
GUI's GPL-3.0 license; it does not copy or trace an existing icon's pixels or path.
Documentation links to absent original logos are removed rather than populated
with unverified copies.

The source snapshot previously referenced an absent optional
`PcrPlanningSession` in two places. r24 removes the `TryBeginOwner` guard in
`PcrAlphaOwner.RequestStartAsync` and the `IsSyncBusy` predicate in
`TaskQueueViewModel.CanEditPcrTarget`. Owner locks, task/device-running checks,
Start authorization, settings persistence and game pipelines are retained.
The first actual offline build also found an absent `PcrPlanningViewModel` and
`PcrPlanningView`. r24 excludes their two optional view-model declarations and
three-line Planning expander. It does not adopt the current experimental module.
The sole remaining cleanup of the absent optional `_pcrPlanning` field is also
excluded; other discovery, connection and timer cleanup is retained. The final
reference scan is case-insensitive.
The original failed build evidence is retained separately.
These are new source changes, not claims that the r20 source was already complete
or that its old DLL hashes prove this rebuild.

The prior GUI displayed a hardcoded 2.16.2 while its Core assembly declared
2.14.0 and its Desktop assembly declared 1.0.0. The new local GUI declares and
displays 2.16.3 consistently. Package revision r24 remains a separate update
identifier. No third-party package or native runtime version is upgraded.
