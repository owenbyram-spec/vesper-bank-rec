#!/usr/bin/env python3
"""Start this Vesper application in its own environment; never install globally."""

from __future__ import annotations
import argparse
import hashlib
import importlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import venv
import webbrowser

PACKAGE = "vesper_bank_rec"
MODULE = "app"
DEFAULT_PORT = 8765
ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Install Python 3.12 or newer from https://www.python.org/downloads/ and try again.")
    parser = argparse.ArgumentParser(description="Run Bank Rec locally")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path, help="Optional explicit local application-data directory")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--no-install", action="store_true", help="Require the already prepared local environment"
    )
    parser.add_argument(
        "--testing",
        action="store_true",
        help="Isolated verification only; requires --data-dir and uses memory secrets",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("Choose a local port from 1024 through 65535.")
    if args.testing and not args.data_dir:
        raise SystemExit("Verification mode requires an explicit --data-dir.")
    env_root = ROOT / ".venv"
    python = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    lock = ROOT / "requirements.lock"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    stamp = env_root / ".vesper-dependencies"
    if not python.exists():
        if args.no_install:
            raise SystemExit("The local environment is missing. Run again without --no-install.")
        print("Preparing this tool's private Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True, symlinks=(os.name != "nt")).create(env_root)
    if not stamp.exists() or stamp.read_text().strip() != digest:
        if args.no_install:
            raise SystemExit("Local dependencies need preparation. Run again without --no-install.")
        print("Installing the pinned application packages from PyPI...", flush=True)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "-r",
                str(lock),
            ],
            check=True,
        )
        stamp.write_text(digest + "\n")
    if Path(sys.prefix).resolve() != env_root.resolve():
        result = subprocess.run([str(python), str(ROOT / "run.py"), *sys.argv[1:]], cwd=ROOT)
        raise SystemExit(result.returncode)
    return serve_source(args)


def serve_source(args):
    """Share desktop ownership so a source launch cannot duplicate a bundled app."""
    from vesper_bank_rec.desktop import (
        InstanceLock, clear_instance_metadata, existing_instance_url,
        find_existing_instance, write_instance_metadata,
    )

    module = importlib.import_module(PACKAGE + "." + MODULE)
    data_dir = (args.data_dir or module.default_data_dir()).resolve()
    lock = InstanceLock(data_dir)
    if not lock.acquire():
        url = existing_instance_url(data_dir, testing=args.testing)
        print(f"Bank Rec is already running at {url}. No second copy was started.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    sock, runtime = None, None
    try:
        existing = find_existing_instance(data_dir, [args.port, DEFAULT_PORT], testing=args.testing)
        if existing:
            print(f"Bank Rec is already running at {existing}. No second copy was started.", flush=True)
            if not args.no_browser:
                webbrowser.open(existing)
            return 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # Allow immediate restart after accepted connections enter TIME_WAIT.
            # An active listener still owns the address; do not enable SO_REUSEPORT.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", args.port))
            sock.listen(128)
        except OSError:
            raise SystemExit(
                f"Local port {args.port} is in use. Open the existing app or use --port with another number. No process was stopped."
            ) from None
        app = module.create_app(data_dir=data_dir, port=args.port, testing=args.testing)
        runtime = app.state.runtime
        write_instance_metadata(data_dir, runtime)
        url = f"http://127.0.0.1:{args.port}"
        print(f"Source folder: {ROOT}", flush=True)
        print(f"Data folder: {data_dir}", flush=True)
        print("Credential mode: verification memory only" if args.testing else "Credential mode: normal (choose storage in settings)", flush=True)
        print(f"Bank Rec is opening at {url}. Keep this window open; press Ctrl+C to stop.", flush=True)
        if not args.no_browser:
            def open_when_ready():
                import urllib.request
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(60):
                    try:
                        with opener.open(url, timeout=0.5):
                            webbrowser.open(url)
                        return
                    except (OSError, TimeoutError):
                        time.sleep(0.2)
            threading.Thread(target=open_when_ready, daemon=True).start()
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=args.port, access_log=False,
            log_level="warning", proxy_headers=False,
        ))
        server.run(sockets=[sock])
        return 0
    finally:
        if runtime is not None:
            clear_instance_metadata(data_dir, runtime["instance_id"])
        if sock is not None:
            sock.close()
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Local app stopped. Saved work is preserved.")
        raise SystemExit(0)
    except ValueError as exc:
        from vesper_bank_rec.desktop import DesktopError
        if isinstance(exc, DesktopError):
            raise SystemExit(str(exc)) from None
        raise
    except subprocess.CalledProcessError:
        raise SystemExit(
            "Package setup did not finish. Check the network connection and rerun; existing work is preserved."
        )
