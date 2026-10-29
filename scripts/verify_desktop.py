#!/usr/bin/env python3
"""Exercise a local bundled candidate with an empty PATH and isolated synthetic data.

This is a developer smoke check, not a substitute for a quarantined native-open
test. It does not remove quarantine, bypass Gatekeeper, or contact a provider.
"""

from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--synthetic-backup", type=Path)
    parser.add_argument("--interactive", action="store_true", help="Pause for a browser inspection before quit/restart")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    executable = bundle / "Contents" / "MacOS" / "Vesper Bank Rec" if bundle.suffix == ".app" else bundle / "Vesper Bank Rec.exe"
    if not executable.is_file():
        raise SystemExit("Supply the complete local desktop bundle.")
    output = ROOT / "exports" / "desktop-verification"
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="frozen-", dir=output))
    data_dir = work / "data"
    environment = dict(os.environ)
    environment["PATH"] = ""
    for name in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    command = [str(executable), "--testing", "--no-browser", "--port", "0", "--data-dir", str(data_dir)]
    process = None
    opener, url, csrf = None, None, None

    def request(path, body=None):
        headers = {}
        if body is not None:
            headers = {"Origin": url, "X-Vesper-CSRF": csrf, "Content-Type": "application/json"}
        req = Request(url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with opener.open(req, timeout=10) as response:
            content = response.read()
            return content if path.startswith("/api/export/xlsx") else json.loads(content)

    def start():
        nonlocal process, opener, url, csrf
        process = subprocess.Popen(command, cwd=work, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        metadata = data_dir / ".vesper-desktop.json"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("The bundled app exited before becoming ready.")
            try:
                receipt = json.loads(metadata.read_text())
                url = "http://127.0.0.1:" + str(receipt["port"])
                opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
                with opener.open(url + "/", timeout=0.5):
                    pass
                bootstrap = request("/api/bootstrap")
                csrf = bootstrap["csrf_token"]
                runtime = bootstrap["runtime"]
                assert bootstrap["desktop_mode"] and runtime["packaged"]
                assert runtime["application"] == "vesper-bank-rec" and runtime["testing"]
                assert runtime["data_dir"] == str(data_dir.resolve())
                assert Path(runtime["source_dir"]).is_relative_to(bundle)
                return runtime
            except (OSError, ValueError, KeyError):
                time.sleep(0.1)
        raise RuntimeError("The bundled app did not become ready.")

    def stop():
        request("/api/app/quit", {})
        assert process.wait(timeout=15) == 0
        assert not (data_dir / ".vesper-desktop.json").exists()

    evidence = {"bundle": str(bundle), "path_empty": True, "no_provider_requests": True, "data_dir": str(data_dir)}
    try:
        first = start()
        assert request("/api/state")["workspaces"] == []
        if args.synthetic_backup:
            backup = json.loads(args.synthetic_backup.read_text())
            assert backup["workspace"]["synthetic"] is True, "Use synthetic evidence only."
            request("/api/restore", backup)
        else:
            request("/api/demo", {})
        before = request("/api/state")
        request_headers = {"Origin": url, "X-Vesper-CSRF": csrf, "Content-Type": "application/json", "X-Vesper-Scope": json.dumps(before["scope"])}
        req = Request(url + "/api/assistant", data=json.dumps({"message": "Explain this synthetic evidence.", "rehearsal": True}).encode(), headers=request_headers)
        with opener.open(req, timeout=10) as response:
            rehearsal = json.load(response)
        assert rehearsal["mode"] == "synthetic-rehearsal" and rehearsal["usage"]["provider_calls"] == 0
        assert rehearsal["usage"]["tool_calls"] == 4
        assert request("/api/state")["workspace"] == before["workspace"]
        query = urlencode({"workspace_id": before["workspace"]["id"], "session_id": before["session"]["id"], "input_version": before["session"]["input_version"], "revision": before["workspace"]["version"]})
        xlsx = request("/api/export/xlsx?" + query)
        (work / "packaged-export.xlsx").write_bytes(xlsx)
        sheet = load_workbook(io.BytesIO(xlsx)).active
        amounts = [row[3] for row in list(sheet)[1:] if row[3].value is not None]
        assert amounts and all(cell.data_type == "n" for cell in amounts)
        assert all(cell.data_type != "f" for row in sheet for cell in row)
        repeated = subprocess.run(command, cwd=work, env=environment, timeout=20, capture_output=True)
        assert repeated.returncode == 0 and process.poll() is None
        assert request("/api/bootstrap")["runtime"]["instance_id"] == first["instance_id"]
        source = subprocess.run([str(ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), str(ROOT / "run.py"), "--no-install", "--no-browser", "--testing", "--data-dir", str(data_dir)], cwd=ROOT, timeout=20, capture_output=True)
        assert source.returncode == 0 and b"already running" in source.stdout
        assert request("/api/bootstrap")["runtime"]["instance_id"] == first["instance_id"]
        evidence.update({"first_instance": first["instance_id"], "url": url, "duplicate_reused": True, "source_launcher_reused": True, "summary": before["summary"], "numeric_excel_cells": len(amounts), "xlsx_sha256": hashlib.sha256(xlsx).hexdigest()})
        print("Packaged app ready at " + url, flush=True)
        if args.interactive:
            input("Inspect the packaged app in the browser, then press Return to verify quit and restart: ")
        if process.poll() is None:
            stop()
        else:
            assert process.returncode == 0
            assert not (data_dir / ".vesper-desktop.json").exists()
            evidence["quit_via_browser"] = True
        second = start()
        assert second["instance_id"] != first["instance_id"]
        assert request("/api/state")["workspace"] == before["workspace"]
        stop()
        evidence.update({"second_instance": second["instance_id"], "quit_exit_zero": True, "restart_preserved_finance_and_history": True})
        (work / "RESULT.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print("PASS: bundled runtime, isolated startup, rehearsal tools, numeric export, relaunch, source reuse, quit and restart.")
        print("Evidence: " + str(work / "RESULT.json"))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
