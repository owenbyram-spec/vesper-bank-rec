"""Source boundaries and native packaging commands; no signing or provider access."""

from copy import deepcopy
from email.message import Message
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from zipfile import ZipFile

import pytest

from scripts import build_desktop as build


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "repository"
    files = {
        "pyproject.toml": '[project]\nversion = "0.2.0"\n',
        "requirements.lock": "fastapi==0.162.0\n",
        "requirements-build.lock": "pyinstaller==6.22.3\n",
        "packaging/desktop_entry.py": "from vesper_bank_rec.desktop import main\nmain()\n",
        "packaging/READ-ME-FIRST.txt": "Local candidate.\n",
        "scripts/build_desktop.py": "# synthetic builder input\n",
        "vesper_bank_rec/__init__.py": '__version__ = "0.2.0"\n',
        "vesper_bank_rec/desktop.py": "def main(): return 0\n",
        "vesper_bank_rec/static/index.html": "<html>synthetic</html>",
        "vesper_bank_rec/static/app.js": "// synthetic",
        "vesper_bank_rec/static/style.css": "body{color:navy}",
        "vesper_bank_rec/.env": "NEVER-SHIP-SYNTHETIC-SECRET",
        "vesper_bank_rec/local-data/private.py": "NEVER-SHIP-SYNTHETIC-CLIENT-DATA",
        "vesper_bank_rec/static/export.xlsx": "NEVER-SHIP-SYNTHETIC-EXPORT",
        "vesper_bank_rec/tests/test_private.py": "NEVER-SHIP-TESTS",
        "vesper_bank_rec/__pycache__/old.pyc": "NEVER-SHIP-CACHE",
        "exports/private.xlsx": "NEVER-SHIP-REPO-EXPORT",
        ".git/config": "NEVER-SHIP-GIT",
    }
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    return root


def test_allowlist_snapshot_excludes_environment_data_exports_history_and_tests(source, tmp_path):
    stage = tmp_path / "stage"
    manifest = build.snapshot(source, stage, "macOS-arm64")
    names = {entry["path"] for entry in manifest["source_files"]}
    assert names == {
        "vesper_bank_rec/__init__.py", "vesper_bank_rec/desktop.py",
        "vesper_bank_rec/static/index.html", "vesper_bank_rec/static/app.js",
        "vesper_bank_rec/static/style.css",
    }
    for path in stage.rglob("*"):
        if path.is_file():
            assert b"NEVER-SHIP" not in path.read_bytes()
    assert manifest["vesper_software_license_added"] is False
    assert manifest["version"] == "0.2.0"
    assert not (stage / "LICENSE").exists()


def test_source_manifest_is_deterministic_and_changes_when_included_source_changes(source, tmp_path):
    first = build.snapshot(source, tmp_path / "first", "macOS-arm64")
    second = build.snapshot(source, tmp_path / "second", "macOS-arm64")
    assert first == second
    assert first["source_sha256"] == build.canonical_digest(first["source_files"])
    (source / "vesper_bank_rec/static/app.js").write_text("// changed synthetic UI")
    third = build.snapshot(source, tmp_path / "third", "macOS-arm64")
    assert third["source_sha256"] != first["source_sha256"]
    for record in third["source_files"]:
        assert record["sha256"] == build.sha256(source / record["path"])


def test_source_symlink_and_missing_ui_fail_closed(source):
    script = source / "vesper_bank_rec/static/app.js"
    script.unlink()
    with pytest.raises(build.BuildError, match="missing"):
        build.source_files(source)
    script.symlink_to(source / "vesper_bank_rec/desktop.py")
    with pytest.raises(build.BuildError, match="symlink"):
        build.source_files(source)


@pytest.mark.parametrize("system,machine,target", [
    ("darwin", "arm64", "macOS-arm64"), ("darwin", "x86_64", "macOS-x86_64"),
    ("win32", "AMD64", "Windows-x64"), ("win32", "ARM64", "Windows-arm64"),
])
def test_target_names_are_native_and_explicit(system, machine, target):
    assert build.target_name(system, machine) == target


def test_unsupported_build_host_is_not_misrepresented_as_a_cross_build():
    with pytest.raises(build.BuildError, match="cross-compilation"):
        build.target_name("linux", "x86_64")


def run_spec(text, monkeypatch):
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.copy_metadata = lambda name: []
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    calls = {}

    def factory(name):
        def record(*args, **kwargs):
            calls[name] = deepcopy(kwargs)
            return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
        return record

    namespace = {name: factory(name) for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")}
    exec(compile(text, "synthetic-desktop.spec", "exec"), namespace)
    return calls


def test_macos_spec_is_windowed_onedir_with_scoped_assets_and_version(source, tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    build.snapshot(source, stage, "macOS-arm64")
    calls = run_spec(build.spec_text(stage, "0.2.0", "macOS-arm64"), monkeypatch)
    assert calls["EXE"]["console"] is False
    assert calls["EXE"]["exclude_binaries"] is True
    assert calls["EXE"]["target_arch"] == "arm64"
    assert calls["EXE"]["codesign_identity"] is None
    assert calls["COLLECT"]["name"] == "Vesper Bank Rec"
    assert calls["BUNDLE"]["bundle_identifier"] == build.BUNDLE_ID
    assert calls["BUNDLE"]["info_plist"]["CFBundleVersion"] == "0.2.0"
    assert calls["BUNDLE"]["info_plist"]["LSUIElement"] is True
    assert calls["Analysis"]["pathex"] == [str(stage / "source")]
    assert calls["Analysis"]["datas"] == [
        (str(stage / "source/vesper_bank_rec/static"), "vesper_bank_rec/static"),
        (str(stage / "SOURCE-MANIFEST.json"), "."),
        (str(stage / "THIRD-PARTY-NOTICES.txt"), "."),
    ]
    assert "uvicorn.loops.asyncio" in calls["Analysis"]["hiddenimports"]
    assert "keyring.backends.macOS" in calls["Analysis"]["hiddenimports"]


def test_windows_spec_has_native_backend_and_version_resource(source, tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    build.snapshot(source, stage, "Windows-x64")
    calls = run_spec(build.spec_text(stage, "0.2.0", "Windows-x64"), monkeypatch)
    assert "BUNDLE" not in calls
    assert calls["EXE"]["console"] is False
    assert calls["EXE"]["version"] == str(stage / "windows-version-info.txt")
    assert "keyring.backends.Windows" in calls["Analysis"]["hiddenimports"]
    resource = (stage / "windows-version-info.txt").read_text()
    compile(resource, "windows-version-info.txt", "eval")
    assert "filevers=(0, 2, 0, 0)" in resource
    assert "'ProductVersion', '0.2.0'" in resource


def test_custom_icon_is_hashed_and_supplied_to_bundle(source, tmp_path, monkeypatch):
    icon = source / "packaging/app-icon.icns"
    icon.write_bytes(b"synthetic-icon")
    stage = tmp_path / "stage"
    manifest = build.snapshot(source, stage, "macOS-arm64")
    assert manifest["build_inputs_sha256"]["packaging/app-icon.icns"] == build.sha256(icon)
    calls = run_spec(build.spec_text(stage, "0.2.0", "macOS-arm64"), monkeypatch)
    assert calls["BUNDLE"]["icon"] == str(stage / "app-icon.icns")


def test_bundle_manifest_preserves_internal_symlinks_and_rejects_escape(tmp_path):
    bundle = tmp_path / "Synthetic.app"
    bundle.mkdir()
    (bundle / "real").write_text("synthetic payload")
    (bundle / "alias").symlink_to("real")
    manifest = build.bundle_manifest(bundle)
    assert manifest["files"][0] == {"path": "alias", "symlink": "real"}
    assert manifest["sha256"] == build.canonical_digest(manifest["files"])
    (bundle / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(build.BuildError, match="escapes"):
        build.bundle_manifest(bundle)


def test_macos_archive_uses_ditto_to_preserve_bundle_links(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    build.archive_release(tmp_path / "release", tmp_path / "release.zip", mac=True)
    command, options = calls[0]
    assert command[:6] == ["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(tmp_path / "release")]
    assert options["check"] is True


def test_portable_windows_zip_retains_whole_folder(tmp_path):
    folder = tmp_path / "Vesper Bank Rec"
    (folder / "Vesper Bank Rec/_internal").mkdir(parents=True)
    (folder / "Vesper Bank Rec/Vesper Bank Rec.exe").write_bytes(b"synthetic executable")
    (folder / "Vesper Bank Rec/_internal/dependency.dll").write_bytes(b"synthetic dependency")
    output = tmp_path / "portable.zip"
    build.archive_release(folder, output, mac=False)
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "Vesper Bank Rec/Vesper Bank Rec/Vesper Bank Rec.exe",
            "Vesper Bank Rec/Vesper Bank Rec/_internal/dependency.dll",
        }


def test_notices_preserve_dependency_and_python_license_without_adding_vesper_license(source, tmp_path, monkeypatch):
    notice = tmp_path / "LICENSE-dependency.txt"
    notice.write_text("Synthetic dependency license text")
    distribution = SimpleNamespace(metadata={"Name": "fastapi"}, version="0.162.0", files=[Path("LICENSE.txt")], locate_file=lambda file: notice)
    monkeypatch.setattr(build.metadata, "distributions", lambda: [distribution])
    python_license = tmp_path / "LICENSE.txt"
    python_license.write_text("Synthetic Python license text")
    monkeypatch.setattr(build.sysconfig, "get_path", lambda name: str(tmp_path))
    destination = tmp_path / "THIRD-PARTY-NOTICES.txt"
    versions = build.third_party_notices(source, destination)
    text = destination.read_text()
    assert versions == {"fastapi": "0.162.0"}
    assert "Synthetic dependency license text" in text
    assert "Synthetic Python license text" in text
    assert "do not add a software license for Vesper" in text
    assert not (source / "LICENSE").exists()


def test_notice_selection_rejects_license_module_source_and_bytecode_but_retains_declared_documents():
    information = Message()
    information["Name"] = "packaging"
    information["License-File"] = "legal/MIT.txt"
    information["License-File"] = "legal/executable.py"
    files = [Path(name) for name in (
        "packaging/licenses/__init__.py", "packaging/licenses/_spdx.py",
        "packaging/licenses/__pycache__/_spdx.cpython-312.pyc",
        "packaging/licenses/README.md", "packaging/licenses/table.json",
        "packaging-26.3.dist-info/licenses/LICENSE", "packaging-26.3.dist-info/licenses/LICENSE.APACHE",
        "packaging-26.3.dist-info/licenses/LICENSE.BSD", "packaging-26.3.dist-info/licenses/NOTICE.txt",
        "packaging-26.3.dist-info/licenses/legal/MIT.txt",
        "packaging-26.3.dist-info/licenses/legal/executable.py",
    )]
    distribution = SimpleNamespace(metadata=information, files=files)
    selected = {str(file) for file in build.legal_notice_files(distribution)}
    assert selected == {
        "packaging-26.3.dist-info/licenses/LICENSE",
        "packaging-26.3.dist-info/licenses/LICENSE.APACHE",
        "packaging-26.3.dist-info/licenses/LICENSE.BSD",
        "packaging-26.3.dist-info/licenses/NOTICE.txt",
        "packaging-26.3.dist-info/licenses/legal/MIT.txt",
    }


def test_notices_output_omits_code_and_binary_in_license_named_package(source, tmp_path, monkeypatch):
    files = {
        "fastapi/licenses/__init__.py": b"CODE-MUST-NOT-BE-NOTICES",
        "fastapi/licenses/__pycache__/module.pyc": b"\x00BINARY-MUST-NOT-BE-NOTICES",
        "fastapi.dist-info/licenses/LICENSE.txt": b"Synthetic license terms",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    distribution = SimpleNamespace(metadata={"Name": "fastapi"}, version="0.162.0", files=list(map(Path, files)), locate_file=lambda file: tmp_path / file)
    monkeypatch.setattr(build.metadata, "distributions", lambda: [distribution])
    (tmp_path / "LICENSE.txt").write_text("Synthetic Python terms")
    monkeypatch.setattr(build.sysconfig, "get_path", lambda name: str(tmp_path))
    output = tmp_path / "NOTICES.txt"
    build.third_party_notices(source, output)
    text = output.read_text()
    assert "Synthetic license terms" in text
    assert "Synthetic Python terms" in text
    assert "MUST-NOT-BE-NOTICES" not in text
    assert "\x00" not in text


@pytest.mark.parametrize("content", [b"\x00binary notice", b"\xffinvalid utf8", b"   \n"])
def test_nontext_notice_fails_closed_instead_of_lossy_decoding(tmp_path, content):
    path = tmp_path / "LICENSE.txt"
    path.write_bytes(content)
    with pytest.raises(build.BuildError, match="review it before bundling"):
        build.legal_notice_text(path, "Synthetic dependency")


def test_build_lock_is_hash_pinned_and_separate_from_production_requirements():
    lock = (build.ROOT / "requirements-build.lock").read_text()
    assert f"pyinstaller=={build.PYINSTALLER_VERSION}" in lock
    assert "--hash=sha256:" in lock
    runtime = (build.ROOT / "requirements.lock").read_text()
    assert "pyinstaller==" not in runtime
