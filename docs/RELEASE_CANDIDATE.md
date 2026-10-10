# Source candidates

The default public CLI source candidate below and the local desktop source companion are separate materials. Neither is a player runtime package or publication approval.

## Default public CLI candidate

Build from the source directory using an existing prepared interpreter:

```powershell
.venv/Scripts/python.exe -B scripts/build_source_candidate.py --root . --output .runtime/source-candidate-UNIQUE
```

Always choose a new output directory. Existing outputs are never overwritten.
The exact `PUBLIC_FILES` allowlist includes the reviewed public README, four
console BAT entrypoints, v2 configuration template, DEVICE and INSPECT_DAILY
instructions, OCR provenance README, existing source modules (including
local_config and onekey_history), pipelines, notices and builder tests.
No recursive inclusion is used. New runtime modules or pipelines require review;
unlisted runtime files block the candidate. Additional ordinary documents must
be read and explicitly selected before they can enter the archive.

The public README has been rewritten and reviewed for the source-directory
workflow; `public_readme_not_reviewed` is retired for this reviewed replacement.
README and every newly selected text still undergo the same privacy scan.
This does not clear any resource, machine-path, device or business gate.
DEVICE and INSPECT_DAILY retain historical implementation and verification notes,
including the pending configurable-serial schedule integration. They are public
text, not current device evidence; their historical details need owner updates
when that integration changes. No private text is silently rewritten into a
second public copy. REUSE and third-party notices retain their existing scope;
the OCR README only records upstream links, not an independently verified ONNX
conversion, exact weight hash or redistribution authorization.

`manifest.json` records each ZIP path, byte size and SHA-256. The ZIP excludes
the manifest itself, game images, private fixtures, models, personal configuration,
ledgers, test artifacts, environments and Git data. Only the OCR text README is
selected from the model directory. No missing assets are replaced by placeholders.
Symlinks, reparse points, source hardlinks (`nlink > 1`), escaped paths and unstable
reads stop construction. Output containment is rechecked after normalization,
before mkdir; hardlinks are rejected before content reads. Two complete source
passes check snapshot stability. Later edits require a fresh build, not reuse of
an unchanged Git HEAD as evidence.

The expected real result remains **BLOCKED (exit 3)**. Game template images and
exact OCR weights lack an established public distribution basis. The legacy CLI
default now derives the default MuMu directory from Windows ProgramFiles; GUI
connections remain player-selected. This resolved path finding does not clear
the remaining asset or unreviewed runtime-module gates. A source ZIP is not a wheel or a GUI installer; runtime resources currently
resolve relative to the source directory. Clean installation, distributable
resource loading and the full device chain remain unverified. `daily_complete=false`.

Exit 0 means READY_FOR_REVIEW, still requiring human review; exit 2 means unsafe,
missing or unstable source input stopped construction; exit 3 means release is
blocked. Sensitive-content findings suppress the ZIP and report only category,
path and line, never the detected value. Quoted nonempty credential values in
JSON/YAML/TOML are covered by bounded patterns, not a complete secret detector.

After concurrent source changes settle, rebuild into a new directory and compare
all manifest hashes against the current files. A changed source snapshot is
pending rebuild. Verify the actual ZIP's inventory, size and hashes, then test
safe entrypoints from its extraction. Dependency reuse is a local smoke check,
not clean installation. Public distribution requires separate review of assets,
licenses and real runtime behavior; no candidate authorizes publication.

## Local desktop source companion

Use the existing interpreter and a new output directory:

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/build_source_candidate.py --root . --output .runtime/gui-source-UNIQUE --gui-source-companion
```

This mode selects the exact paths in `docs/gui-source-files.json`: the twelve
desktop GUI projects, their actual compile/resource inputs, the current Python
Agent/business modules, Maa pipelines, interface and maintenance entrypoints.
It includes explicitly listed build icons, fonts, sound and parser/shader data as
binary or text resources. It excludes `bin`, `obj`, Android/Updater projects,
game images, OCR weights, environments, personal configuration, logs and ledgers.
New source inputs need an explicit inventory review; they are not included by
recursively archiving the repository.

The same stable reads, path/link checks, SHA-256 inventory, deterministic ZIP and
text privacy checks apply. Reference-only XAML password bindings are checked as
bindings; literal credentials and private paths still block the archive. The
default public mode and its game/OCR gates remain unchanged. Every GUI companion
manifest fixes `distribution_ready=false`, even when its source snapshot is
`READY_FOR_REVIEW`. A `BLOCKED` result may still produce a local ZIP for a
machine-path finding; sensitive-content findings suppress it. Review the
manifest result, findings and `missing_declared_content`, not only ZIP existence.

The build entrypoint is
`gui/MFAAvalonia/MFAAvalonia.Desktop/MFAAvalonia.Desktop.csproj`, not the full
solution containing other platforms. With a prepared .NET 10 SDK and reviewed
dependencies, restore that project and then build it in Release. For an offline
check, pass an explicit NuGet config whose only source is an existing local
package cache. Start from the extracted ZIP without old `obj`/`bin`; do not reuse
generated build inputs as proof that the source archive is complete.

Compilation verifies this managed-source snapshot. It does not establish exact
binary reproducibility, native DLL source correspondence, third-party/model/game
asset rights, a clean player installation or successful device execution. The
current desktop project declares a documentation logo that is absent from the
checkout; the manifest records that missing item without a placeholder.
Archive hashes, build commands, dependency graph comparison and source changes
since the prior candidate are retained in the private local verification receipt.
Player runtime delivery remains subject to the qualifications in `BETA_LOCAL.md`.

The desktop source inventory also selects `docs/licenses/COMPONENTS.json` and sixteen exact NuGet license texts from the prepared cache. The inventory records the shipped dependency graph, byte comparisons and outstanding evidence without private cache paths. Updating these selected documents requires a fresh source candidate. It does not change runtime behavior or distribution readiness.

### Run the included offline tests

The desktop companion includes six existing Python test modules plus
`tests/conftest.py`: combination validation, Owner grants and source-channel
revocation, player result/error text, and manifest-only runtime archive export.
Their inputs are synthetic; they need no game images, OCR weights, emulator,
private history, previous build output, or special `PCR_*` environment variables.
The shared fixtures reject real ADB controller construction/device discovery and
place new logs and temporary files under the extracted tree's `.runtime`.

With an existing environment containing the dependencies in `requirements.txt`,
run this command **from the extracted source root**, using that environment's
Python executable:

```powershell
python -X utf8 -B -m pytest -q -rs
```

The default `pyproject.toml` selection runs all tests included in this companion.
Do not point `PYTHONPATH` back at the development checkout. On Windows, a symlink
case may skip when creation is unavailable; pytest reports the precise reason.
No global Python or permissions changes are needed. The archive/export tests
load `packaging`, supplied by pytest's declared dependencies.

These tests exercise Python contracts and packaging, not native Agent TCP,
recognition accuracy, GUI rendering, real gameplay or clean-machine installation.
The private C# acceptance harness and image replays still require separately
controlled assets/evidence and are excluded. Adding a test requires checking its
fixture/import closure and updating the exact inventory and builder allowlist;
the builder rejects a companion missing the selected suite or its shared
isolation fixtures. Test source still passes the ordinary privacy scan.

## Private player archive

The local GUI candidate is separate from the source ZIP. Keep its existing
runtime data, and export only its verified manifest payload into a new ZIP:

```powershell
.venv/Scripts/python.exe -X utf8 -B scripts/build_pcr_player_candidate.py --package <existing-private-portable-candidate> --archive <new-zip-path>
```

The export includes the player guide and package manifest, verifies every entry's
size and SHA-256, and omits unlisted configuration, logs and journals. It rejects
a checkout-bound entry, stale or missing payload, unsafe paths and an existing
output. A successful local archive remains `distribution_ready=false`; it does
not approve sharing assets or running an unsettled account from a new directory.
