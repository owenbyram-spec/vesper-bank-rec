# Deferred desktop experiment

On 2026-10-28, Owen cancelled Apple enrollment and chose the local server/browser workflow. Use [Quick start](QUICK-START.md) for current setup. The code, local candidates and observations below are preserved as historical development evidence. They are not the current delivery plan. No Apple account, signing or notarization is needed for the chosen local-server path, and no public hosted service is claimed.

The desktop package includes Python and its application dependencies. End users do not install Python, run terminal commands or download packages to launch it. The browser is the app's interface; the backend runs locally until **Quit app** stops it.

## Candidate distribution boundary

The configured macOS candidate is version **0.2.0**, targeting **Apple Silicon / arm64**. The default build uses ad-hoc signing, with `public_release: false` in its artifact manifest. It is not Developer ID signed or notarized and is not a verified public download for beginners. Do not treat a successful local launch as a fresh-download Gatekeeper pass. Developer ID signing and notarization were unavailable during the experiment and remain unverified. No signed/notarized public desktop-download URL is claimed here.

An earlier candidate was downloaded through Chrome over HTTP, retained quarantine metadata, and extracted natively with its bundle intact. Finder Open did not start it, and the Gatekeeper policy assessment rejected it with exit code 3. That candidate was superseded by the rebuilt final candidate recorded below; this earlier archive is not the final artifact. Final artifact hashes and observations belong in [Verification](VERIFICATION.md).

The native build recipe also describes Windows output. Windows build execution, launch and signing remain unverified. An Intel Mac build and a supported minimum macOS version have not been verified; the build target alone does not establish native-library compatibility on older systems.

The previously published GitHub **Source code** ZIP is separate from the desktop ZIP and still requires Python. No software license has been added for Vesper's own source. The bundled Python and dependencies retain their own notices; native-library notice completeness needs review before a public release.

## Proposed end-user path if desktop work resumes

Once the distribution gates below pass, the macOS path is:

1. Download the desktop ZIP for the Mac's architecture and extract it completely.
2. Open **Vesper Bank Rec.app**. Its bundled runtime starts the local service and opens the default browser.
3. Resume the synthetic example or select a saved client workspace. Connect AI only if wanted; calculations and exports work without it.
4. Choose **Quit app** at the top of Bank Rec when finished. Wait for any assistant request, connection check or accounting refresh to finish first. Closing the browser tab alone leaves the service running.

Saved work lives in the operating system's application-data directory outside the bundle. Moving or replacing the app does not move the saved records. Session-only credentials are lost on quit; remembered credentials remain scoped to the workspace and resolved data path. Current source and desktop launchers share the same data-directory lock and verify a running instance before reusing it. Shutdown drains active work before releasing that lock. Close older source copies before switching, including copies running on unknown custom ports. See [Data and privacy](DATA-AND-PRIVACY.md).

If the candidate is blocked by macOS, stop. These instructions do not authorize disabling security controls or bypassing the warning.

## Local build artifacts

The native Apple Silicon build recipe writes these outputs by default:

| Output | Purpose |
| --- | --- |
| `dist/macOS-arm64/Vesper Bank Rec.app` | Complete local app bundle |
| `dist/macOS-arm64/Vesper-Bank-Rec-macOS-Apple-Silicon.zip` | Download candidate, containing the app, read-me, source manifest and third-party notices |
| `dist/macOS-arm64/ARTIFACT-MANIFEST.json` | Archive SHA-256, source fingerprint, signing status and bundle file hashes; stored beside the ZIP |

`SOURCE-MANIFEST.json` identifies the included source, version and dependencies. The builder takes an allowlisted source snapshot; it does not package local finance data, exports, credentials, `.venv` or repository history. Verify the actual output and manifests before distribution. Generated outputs are local build artifacts, not automatically published releases.

Developer build commands on a native macOS host:

```sh
python3.12 -m venv .build-venv
.build-venv/bin/python -m pip install --require-hashes -r requirements.lock
.build-venv/bin/python -m pip install --require-hashes -r requirements-build.lock
.build-venv/bin/python scripts/build_desktop.py
```

Build tooling requires Python; running the resulting desktop app does not. Build on the intended native host rather than assuming cross-compilation. Deliberate rebuilds require `--replace` when target output already exists. These build commands do not sign a public release.

An authorized release maintainer must supply a Developer ID signing identity and a locally configured notarization profile for the builder's optional `--codesign-identity` and `--notary-profile` arguments. That step needs the appropriate Apple Developer account and direct owner participation. Never include private keys, account secrets or credential-store contents in source, chat or verification evidence.

## Deferred public-release gates

Apple requires Developer ID signing and notarization for software distributed outside the App Store to run under default Gatekeeper settings. A local ad-hoc signature does not satisfy that distribution requirement. [Apple's code-signing guidance](https://support.apple.com/guide/security/app-code-signing-process-sec3ad8e6e53/web).

Before describing this as a beginner-ready desktop download:

- [ ] Verify the exact allowlisted source snapshot, bundled dependency/native-library notices, and absence of local data and secrets.
- [ ] Build for the intended architecture; record the artifact version, SHA-256 and tested macOS version.
- [ ] Sign the app and bundled code with the authorized Developer ID and appropriate hardened-runtime settings; verify the signature.
- [ ] Receive an accepted notarization result, staple the ticket to the app, validate it, and recreate the final ZIP and hash.
- [ ] Download that exact final ZIP through a browser on a fresh Mac or fresh VM snapshot, with normal quarantine and security settings and without a separate Python installation.
- [ ] Extract and open it as an end user would; verify Gatekeeper, browser startup, sample resume, numeric Excel export, **Quit app** while idle and its busy-operation refusal, drained shutdown, restart persistence and safe repeat opening from both current launchers.
- [ ] Test startup offline to verify the stapled ticket and local synthetic workflow; separately disclose that real AI and QuickBooks need network access.
- [ ] Publish only the reviewed final artifact, then verify a fresh download of the published bytes and record the result in [Verification](VERIFICATION.md).

Apple recommends a fresh machine or VM snapshot for each Gatekeeper test because it caches prior results. A browser download preserves the normal quarantine path. Command-line policy checks can help with diagnosis but do not replace this user-path test. [Apple's testing guidance](https://developer.apple.com/forums/thread/130560).

These gates apply only if desktop distribution is requested again; they are not required work for the current local-server path. They have not passed. Actual local build and test results belong in [Verification](VERIFICATION.md); public-source publication history remains in [the release checklist](../RELEASE-CHECKLIST.md).

## Final local candidate verification

The final 0.2.0 candidate ZIP SHA-256 is `4369aa90777282c3bed38e2524cd7d0748aff6040e1f1dd6f450558cfd0016cf`. The bundled executable passed the no-Python-command-path runtime check, setup-screen inspection, synthetic tools/export, single-instance/source reuse, browser Quit and exact financial continuity across restart. The final Chrome download and native extraction matched the build with quarantine preserved. Finder opening did not start it; macOS signature assessment rejected it (exit 3). Signing/notarization and clean-machine acceptance remain open. Earlier trial artifacts were superseded after review fixes. See [Verification](VERIFICATION.md) for the complete distinction between controlled runtime checks and downloaded-app trust checks.
