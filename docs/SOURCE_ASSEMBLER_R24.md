# r24 Source Delivery Helper

`build_r24_source_delivery.py` uses only the standard library. It does not build or run the GUI, Python Agent, emulator, or game. Network access is not used.

1. `freeze-config --template LOCAL_TEMPLATE.json --output LOCAL_CONFIG.json` resolves only explicit input manifests and overlay rows and freezes sizes/hashes. The config contains local paths and must NOT be distributed.
2. `prepare --config LOCAL_CONFIG.json --output NEW_PREPARED_DIRECTORY` verifies all frozen bytes, constructs a new source tree, writes SOURCE_INVENTORY.json, and verifies the result. It refuses an existing output directory and never deletes another output.
3. `verify --prepared NEW_PREPARED_DIRECTORY --inventory-sha256 SHA256` checks every file, rejects additional files, and scans selected text for private user paths or signed URL queries.
4. `pack --prepared NEW_PREPARED_DIRECTORY --inventory-sha256 SHA256 --archive NEW_SOURCE.zip` creates a new ZIP containing only the inventory files and SOURCE_INVENTORY.json, then verifies archive contents and hashes. Root owns running this final step and delivery to Desktop.

Template keys are `baseline`, `gui`, `business`, `materials`, `stable`, `business_clone`, and `overlays`. Each input spec has `manifest` and optional expected `sha256`; inputs with selected files additionally have `root`. Business also specifies `tests_root`, which must match the two exact paths in the final business source freeze. Each overlay is `{path,root,source,origin}` with optional expected `sha256`; no directory is recursively selected. Accepted origins are r24-source-delivery, r24-root-release-documents, and r24-root-release-helper.

Release mappings preserve dependencies relative to `third_party/materials/`: MATERIALS.json, HANDOFF.md, all 9 source_delivery_whitelist archive paths, and all 106 notices paths. Existing runtime license originals have additional source copies under docs/licenses/runtime/. The actual runtimes remain player payload, not source payload. The legal OCR model owned bytes and README are included by manifest; 9 retired game PNG and 3 retired GUI images are never selected.

For this release, explicit overlays also include scripts/assemble_release.py, scripts/add_provenance.py, scripts/create_release_components.py, scripts/source_gate.py, and the final sealed player manifest at delivery/runtime-package-manifest.json. Freeze that manifest after root sealing, then prepare a NEW directory and pack from its exact new inventory. Never edit a previous freeze or insert the source archive hash into its own manifest chain.

The source inventory contains only relative output paths, source categories, fixed hashes, and counts. It excludes local input roots, signed retrieval audit URLs, dirty checkout traversal, user configuration, logs, ledger, screenshots, bin/obj, and the old .runtime source companion ZIP. Fixed upstream archives are preserved byte-for-byte, not repacked under this project's license.
