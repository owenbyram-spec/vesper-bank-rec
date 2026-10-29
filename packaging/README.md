# Native desktop candidates

This builder packages the local Bank Rec service, browser assets, Python interpreter and required libraries. The person opening the resulting app does not install Python or download packages. AI and QuickBooks remain optional provider connections with the existing workspace, consent and review controls.

Build on the operating system and CPU architecture being targeted. PyInstaller is not a cross compiler. The current local target is Apple Silicon macOS; the Windows recipe produces a portable application folder on a Windows host. A Windows recipe or unit test does not establish that a Windows executable has run successfully.

## Build

Use a clean build environment with Python 3.12 and the production and build locks. Do not build from an environment containing client-specific plugins or editable installations of other projects.

```sh
python3.12 -m venv .build-venv
.build-venv/bin/python -m pip install --require-hashes -r requirements.lock -r requirements-build.lock
.build-venv/bin/python scripts/build_desktop.py
```

On Windows use `.build-venv\Scripts\python.exe` in place of `.build-venv/bin/python`. The default architecture is the native interpreter/host architecture. Build separate artifacts for Intel Macs, Apple Silicon Macs and Windows.

On this Apple Silicon host the outputs are:

- `dist/macOS-arm64/Vesper Bank Rec.app`
- `dist/macOS-arm64/Vesper-Bank-Rec-macOS-Apple-Silicon.zip`
- `SOURCE-MANIFEST.json`, `ARTIFACT-MANIFEST.json`, `THIRD-PARTY-NOTICES.txt` and `READ-ME-FIRST.txt` beside the app.

The archive contains the app, source manifest, notices and opening instructions. macOS archives use `ditto` to preserve bundle symlinks and resource metadata. The external artifact manifest records the final archive SHA256 and byte count, target, version, source digest, signing/notarization state and file hashes/symlink targets for the completed bundle. It cannot contain its own archive hash inside the archive.

Existing output is preserved unless the builder receives `--replace`. Build staging directories are inside ignored `build/`; output is inside ignored `dist/`. The build does not publish, upload, start an app or read financial application-data directories.

## Included inputs

The builder copies recognized Python source and static assets from `vesper_bank_rec/` into a separate staging tree. It excludes environment files, databases, exports, tests, caches and Git history. It rejects source symlinks and escaping bundle symlinks. Only that staged tree is the application import path supplied to PyInstaller. The entry script invokes `vesper_bank_rec.desktop.main()` directly and does not invoke the source launcher or install dependencies at runtime.

The source manifest hashes each included application file and the build inputs. Sorting and canonical serialization make the source digest repeatable for identical input bytes. This does **not** claim that independently built executable files or ZIP timestamps are bit-for-bit reproducible. Version metadata comes from `pyproject.toml` and is also applied to macOS bundle metadata and the Windows executable version resource. Optional `packaging/app-icon.icns` and `packaging/app-icon.ico` are hashed and supplied to the corresponding native build.

Third-party notices are generated from installed distribution license/notice files and CPython's license. They retain their own terms and do not add a software license to Vesper's source. The builder records native-library notice review as incomplete; review the licenses of all bundled native runtime components before a public release.

## Local candidate and public release

The default macOS output is ad-hoc signed for local operation. It is not Developer ID signed, notarized or a verified public download. The default Windows output is unsigned and requires native testing. Every artifact manifest retains `public_release: false`.

An authorized release operator can explicitly provide a macOS signing identity and an already configured notarization profile:

```sh
.build-venv/bin/python scripts/build_desktop.py --replace --codesign-identity '<authorized Developer ID identity>' --notary-profile '<existing authorized profile>'
```

These flags invoke signing/notarization and must be used only when that release action has been authorized. The builder does not list signing identities, inspect credential stores, accept raw signing passwords or create notarization credentials. A supplied notarization profile is passed directly to `notarytool`; successful submission is followed by stapling/validation and regeneration of the archive checksum. Signing and notarization alone do not establish user-facing download behavior.

Before publication, obtain the required signing and native-library license review, test a fresh browser download and native open with normal device protections enabled, and exercise the bundled app's synthetic workflow, export and Quit app behavior. Keep the artifact manifest and checksums with the verification evidence. Never claim Windows, older macOS versions, real provider access or OS-keychain behavior from a build result alone.

Implementation references: [PyInstaller usage and native builds](https://pyinstaller.org/en/stable/usage.html), [spec files and bundled data](https://pyinstaller.org/en/stable/spec-files.html), and [macOS architecture and signing notes](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support).
