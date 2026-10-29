#!/usr/bin/env python3
"""Build native desktop candidates from an allowlisted source-only snapshot.

Build dependencies are installed separately from requirements-build.lock.
Nothing here reads application data, environment secrets, or credential stores.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Vesper Bank Rec"
BUNDLE_ID = "ai.vesperco.bankrec"
PYINSTALLER_VERSION = "6.22.3"
EXCLUDED_PARTS = {".git", ".venv", "__pycache__", "tests", "local-data", "exports", ".aws", ".codex"}
ASSET_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".ico", ".icns", ".woff", ".woff2"}
HIDDEN_IMPORTS = ["uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"]


class BuildError(ValueError):
    pass


def sha256(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_files(root):
    """Only application code and recognized static assets; no recursive repo copy."""
    root = Path(root)
    package = root / "vesper_bank_rec"
    if package.is_symlink():
        raise BuildError("The application source directory must not be a symlink.")
    selected = []
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS or part.startswith(".env") for part in relative.parts):
            continue
        is_asset = relative.parts[1:2] == ("static",) and path.suffix.lower() in ASSET_SUFFIXES
        is_source = "static" not in relative.parts and path.suffix == ".py"
        if not (is_asset or is_source):
            continue
        if path.is_symlink():
            raise BuildError(f"Source inputs must be ordinary files, not symlinks: {relative}")
        if path.is_file():
            selected.append((path, relative))
    names = {relative.as_posix() for _, relative in selected}
    required = {"vesper_bank_rec/desktop.py", "vesper_bank_rec/static/index.html", "vesper_bank_rec/static/app.js", "vesper_bank_rec/static/style.css"}
    if not required <= names:
        raise BuildError("Desktop entry point or required UI assets are missing.")
    return selected


def target_name(system=None, machine=None):
    system, machine = system or sys.platform, (machine or platform.machine()).lower()
    if system == "darwin" and machine in {"arm64", "aarch64", "x86_64", "amd64"}:
        return "macOS-" + ("arm64" if machine in {"arm64", "aarch64"} else "x86_64")
    if system == "win32" and machine in {"amd64", "x86_64", "arm64", "aarch64"}:
        return "Windows-" + ("arm64" if machine in {"arm64", "aarch64"} else "x64")
    raise BuildError("Build on the intended native macOS or Windows host; cross-compilation is unsupported.")


def snapshot(root, stage, target):
    root, stage = Path(root), Path(stage)
    records = []
    for path, relative in source_files(root):
        destination = stage / "source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        records.append({"path": relative.as_posix(), "sha256": sha256(destination), "bytes": destination.stat().st_size})
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    inputs = {}
    for name in ("pyproject.toml", "requirements.lock", "requirements-build.lock", "packaging/desktop_entry.py", "packaging/READ-ME-FIRST.txt", "scripts/build_desktop.py"):
        path = root / name
        inputs[name] = sha256(path)
    icon = root / "packaging" / ("app-icon.icns" if target.startswith("macOS-") else "app-icon.ico")
    if icon.is_file():
        if icon.is_symlink():
            raise BuildError("The desktop icon must not be a symlink.")
        inputs[icon.relative_to(root).as_posix()] = sha256(icon)
        shutil.copyfile(icon, stage / icon.name)
    manifest = {
        "schema_version": 1,
        "product": "vesper-bank-rec",
        "version": version,
        "target": target,
        "source_files": records,
        "source_sha256": canonical_digest(records),
        "build_inputs_sha256": inputs,
        "python": platform.python_version(),
        "pyinstaller": PYINSTALLER_VERSION,
        "vesper_software_license_added": False,
        "native_library_notices_audited": False,
    }
    (stage / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    shutil.copyfile(root / "packaging" / "desktop_entry.py", stage / "desktop_entry.py")
    if target.startswith("Windows-"):
        (stage / "windows-version-info.txt").write_text(windows_version_info(version))
    return manifest


def windows_version_info(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise BuildError("Desktop release versions must use three numeric components.")
    components = tuple(int(part) for part in version.split(".")) + (0,)
    strings = {"CompanyName": "Vesper & Co", "FileDescription": APP_NAME,
               "FileVersion": version, "ProductVersion": version, "ProductName": APP_NAME,
               "OriginalFilename": APP_NAME + ".exe"}
    entries = ", ".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    return (
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={components!r}, prodvers={components!r}, "
        "mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)), "
        f"kids=[StringFileInfo([StringTable('040904B0', [{entries}])]), "
        "VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n"
    )


def locked_names(root):
    text = (Path(root) / "requirements.lock").read_text() + (Path(root) / "requirements-build.lock").read_text()
    return {re.sub(r"[-_.]+", "-", name.lower()) for name in re.findall(r"^([\w.-]+)==", text, re.M)}


def legal_notice_files(distribution):
    """Select notice documents, not Python modules in a package named licenses."""
    get_all = getattr(distribution.metadata, "get_all", lambda name: [])
    declared = {Path(name).as_posix().lstrip("./") for name in get_all("License-File") or []}
    selected = []
    for file in distribution.files or []:
        path = Path(file)
        if "__pycache__" in path.parts or path.suffix.lower() in {
            ".py", ".pyc", ".pyo", ".so", ".pyd", ".dll", ".exe",
        }:
            continue
        filename = path.name.lower()
        named_notice = re.fullmatch(r"(?:licen[cs]e|copying|notice)(?:[._-].*)?", filename)
        # License-File paths are relative to the distribution metadata's license
        # directory. Modern wheels put them under <name>.dist-info/licenses/.
        parts = path.parts
        metadata_index = next((index for index, part in enumerate(parts) if part.endswith(".dist-info")), None)
        metadata_relative = None
        if metadata_index is not None:
            remaining = parts[metadata_index + 1:]
            if remaining[:1] == ("licenses",):
                remaining = remaining[1:]
            metadata_relative = Path(*remaining).as_posix() if remaining else None
        declared_notice = metadata_relative in declared or path.as_posix() in declared
        if named_notice or declared_notice:
            selected.append(file)
    return sorted(selected, key=str)


def legal_notice_text(path, component):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise BuildError(f"Unreadable or oversized dependency notice for {component}.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        raise BuildError(f"Dependency notice for {component} is not UTF-8 text; review it before bundling.") from None
    if "\x00" in text or not text.strip():
        raise BuildError(f"Dependency notice for {component} is empty or binary; review it before bundling.")
    return text


def third_party_notices(root, destination):
    names = locked_names(root)
    sections = [
        "THIRD-PARTY NOTICES\n\nThese notices apply to the named third-party components. "
        "They do not add a software license for Vesper's own source. Build-only tools may be listed. "
        "Bundled native-library notice completeness remains a public-release review gate.\n"
    ]
    versions = {}
    for distribution in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        if re.sub(r"[-_.]+", "-", name.lower()) not in names:
            continue
        versions[name] = distribution.version
        licenses = legal_notice_files(distribution)
        if not licenses:
            raise BuildError(f"No installed license/notice file found for {name}. Review it before bundling.")
        for file in sorted(licenses, key=str):
            path = Path(distribution.locate_file(file))
            sections.append(f"\n{'=' * 72}\n{name} {distribution.version} — {file}\n{'=' * 72}\n" + legal_notice_text(path, name))
    candidates = [Path(sysconfig.get_path("stdlib")) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"]
    python_license = next((path for path in candidates if path.is_file()), None)
    if python_license is None:
        raise BuildError("The bundled Python license could not be located.")
    sections.append(f"\n{'=' * 72}\nCPython {platform.python_version()}\n{'=' * 72}\n" + legal_notice_text(python_license, "CPython"))
    Path(destination).write_text("\n".join(sections), encoding="utf-8")
    return versions


def spec_text(stage, version, target, identity=None):
    """Explicit data/hidden imports; generated spec contains no runtime data paths."""
    stage = Path(stage)
    mac = target.startswith("macOS-")
    arch = target.removeprefix("macOS-") if mac else None
    icon = stage / ("app-icon.icns" if mac else "app-icon.ico")
    icon_path = str(icon) if icon.is_file() else None
    version_file = None if mac else str(stage / "windows-version-info.txt")
    hidden = HIDDEN_IMPORTS + (["keyring.backends.macOS"] if mac else ["keyring.backends.Windows"])
    data = [
        (str(stage / "source" / "vesper_bank_rec" / "static"), "vesper_bank_rec/static"),
        (str(stage / "SOURCE-MANIFEST.json"), "."),
        (str(stage / "THIRD-PARTY-NOTICES.txt"), "."),
    ]
    # keyring discovers its native backend via entry-point metadata at runtime.
    text = "from PyInstaller.utils.hooks import copy_metadata\n"
    text += f"a = Analysis([{str(stage / 'desktop_entry.py')!r}], pathex={[str(stage / 'source')]!r}, binaries=[], datas={data!r} + copy_metadata('keyring'), hiddenimports={hidden!r}, hookspath=[], runtime_hooks=[], excludes=['pytest', 'pip', 'ruff', 'unittest'], optimize=0)\n"
    text += "pyz = PYZ(a.pure)\n"
    text += f"exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name={APP_NAME!r}, debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, argv_emulation=False, target_arch={arch!r}, codesign_identity={identity!r}, entitlements_file=None, icon={icon_path!r}, version={version_file!r})\n"
    text += f"coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name={APP_NAME!r})\n"
    if mac:
        info = {"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleShortVersionString": version, "CFBundleVersion": version, "NSHighResolutionCapable": True, "LSUIElement": True}
        text += f"app = BUNDLE(coll, name={APP_NAME + '.app'!r}, icon={icon_path!r}, bundle_identifier={BUNDLE_ID!r}, info_plist={info!r})\n"
    return text


def bundle_manifest(bundle):
    bundle = Path(bundle)
    entries = []
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(bundle.resolve()):
                raise BuildError(f"Bundle symlink escapes the application: {relative}")
            entries.append({"path": relative, "symlink": os.readlink(path)})
        elif path.is_file():
            entries.append({"path": relative, "sha256": sha256(path), "bytes": path.stat().st_size})
    return {"files": entries, "sha256": canonical_digest(entries)}


def archive_release(folder, output, *, mac):
    if mac:
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(folder), str(output)], check=True)
    else:
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for path in sorted(Path(folder).rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(Path(folder).parent).as_posix())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a bundled desktop candidate on its native OS")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--replace", action="store_true", help="Replace only this target's previous generated output")
    parser.add_argument("--codesign-identity", help="Explicit macOS Developer ID identity; default is local ad-hoc signing")
    parser.add_argument("--notary-profile", help="Explicit previously configured notarytool profile; requires --codesign-identity")
    args = parser.parse_args(argv)
    target = target_name()
    mac = target.startswith("macOS-")
    if (args.codesign_identity or args.notary_profile) and not mac:
        raise BuildError("macOS signing options require a macOS build host.")
    if args.notary_profile and not args.codesign_identity:
        raise BuildError("Notarization requires an explicitly supplied signing identity.")
    if metadata.version("pyinstaller") != PYINSTALLER_VERSION:
        raise BuildError("Install the exact hash-locked desktop build dependencies first.")
    output = args.output_dir.resolve() / target
    if output.exists() and not args.replace:
        raise BuildError(f"Generated output exists: {output}. Use --replace for a deliberate rebuild.")
    if output.is_symlink():
        raise BuildError("The generated output directory must not be a symlink.")
    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="desktop-", dir=work))
    manifest = snapshot(ROOT, stage, target)
    versions = third_party_notices(ROOT, stage / "THIRD-PARTY-NOTICES.txt")
    manifest["dependencies"] = versions
    (stage / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    spec = stage / "desktop.spec"
    spec.write_text(spec_text(stage, manifest["version"], target, args.codesign_identity))
    environment = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(stage / "cache")}
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(stage / "output"), "--workpath", str(stage / "work"), str(spec)], cwd=stage, env=environment, check=True)
    name = APP_NAME + (".app" if mac else "")
    built = stage / "output" / name
    if not built.is_dir():
        raise BuildError("The expected complete desktop bundle was not produced.")
    if mac:
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(built)], check=True)
    release = stage / APP_NAME
    release.mkdir()
    shutil.move(str(built), release / name)
    for file in ("SOURCE-MANIFEST.json", "THIRD-PARTY-NOTICES.txt"):
        shutil.copyfile(stage / file, release / file)
    shutil.copyfile(ROOT / "packaging" / "READ-ME-FIRST.txt", release / "READ-ME-FIRST.txt")
    archive_name = "Vesper-Bank-Rec-" + ("macOS-Apple-Silicon" if target == "macOS-arm64" else target) + ".zip"
    archive = stage / archive_name
    archive_release(release, archive, mac=mac)
    notarized = False
    if args.notary_profile:
        subprocess.run(["xcrun", "notarytool", "submit", str(archive), "--keychain-profile", args.notary_profile, "--wait"], check=True)
        subprocess.run(["xcrun", "stapler", "staple", str(release / name)], check=True)
        subprocess.run(["xcrun", "stapler", "validate", str(release / name)], check=True)
        archive.unlink()
        archive_release(release, archive, mac=True)
        notarized = True
    artifact = {
        "schema_version": 1, "product": "vesper-bank-rec", "version": manifest["version"], "target": target,
        "built_at": datetime.now(timezone.utc).isoformat(), "source_sha256": manifest["source_sha256"],
        "archive": archive_name, "archive_sha256": sha256(archive), "archive_bytes": archive.stat().st_size,
        "signing": "Developer ID requested" if args.codesign_identity else "ad-hoc local candidate" if mac else "unsigned Windows candidate",
        "notarized": notarized, "public_release": False,
        "verification_required": ["fresh browser download and native open", "native-library notice review", "Windows execution on Windows"],
        "bundle": bundle_manifest(release / name),
    }
    (release / "ARTIFACT-MANIFEST.json").write_text(json.dumps(artifact, sort_keys=True, indent=2) + "\n")
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(release), output)
    shutil.move(str(archive), output / archive_name)
    print(f"Desktop candidate: {output / name}")
    print(f"Download archive: {output / archive_name}")
    print(f"Archive SHA256: {artifact['archive_sha256']}")
    print("Public release: no. Review the artifact manifest's remaining verification gates.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, metadata.PackageNotFoundError) as exc:
        raise SystemExit(str(exc)) from None
