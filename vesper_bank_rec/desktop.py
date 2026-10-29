"""Bundled desktop entry: one local process per data directory, with a browser UI.

This module starts the packaged interpreter directly. It never installs Python,
creates a virtual environment, runs pip, or reads ambient provider credentials.
"""

from __future__ import annotations

import argparse
import errno
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, build_opener
import webbrowser


DEFAULT_PORT = 8765
LOCK_NAME = ".vesper-desktop.lock"
METADATA_NAME = ".vesper-desktop.json"
LEGACY_UNVERIFIED = object()


class DesktopError(ValueError):
    """A safe, user-facing startup failure; underlying exception text stays private."""


class InstanceLock:
    """Keep the advisory lock's handle open for the full server lifetime.

    Do not unlink the lock file: another launch may already hold its inode open.
    The OS releases the lock even after an interrupted or crashed application.
    """

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            raise DesktopError("This application already holds its data-directory lock.")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.data_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt

                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                os.fchmod(handle.fileno(), 0o600)
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        self.handle = handle
        return True

    def close(self):
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _read_metadata(data_dir):
    try:
        with (Path(data_dir) / METADATA_NAME).open("rb") as handle:
            content = handle.read(1025)
        if len(content) > 1024:
            return None
        value = json.loads(content)
        if (
            not isinstance(value, dict)
            or set(value) != {"port", "instance_id"}
            or type(value["port"]) is not int
            or not 1024 <= value["port"] <= 65535
            or not isinstance(value["instance_id"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["instance_id"])
        ):
            return None
        return value
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def write_instance_metadata(data_dir, runtime):
    """Publish only a public instance ID and port, atomically and with private mode."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=data_dir, prefix=".vesper-desktop-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            json.dump({"port": runtime["port"], "instance_id": runtime["instance_id"]}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(Path(data_dir) / METADATA_NAME)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def clear_instance_metadata(data_dir, instance_id):
    metadata = _read_metadata(data_dir)
    if metadata and metadata["instance_id"] == instance_id:
        try:
            (Path(data_dir) / METADATA_NAME).unlink(missing_ok=True)
        except OSError:
            # A stale receipt is harmless; only a held OS lock blocks another launch.
            pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


def _read_runtime(port):
    """Read only the loopback app identity; bypass proxies and refuse redirects.

    The cookie jar exists only for these requests. No settings or secret-store
    endpoint is called, and no session cookie or CSRF token is persisted.
    """
    if type(port) is not int or not 1024 <= port <= 65535:
        return None
    url = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), _NoRedirect())
    try:
        with opener.open(url + "/", timeout=0.5) as response:
            if response.status != 200:
                return None
            title = _TitleParser()
            title.feed(response.read(8192).decode("utf-8", errors="replace"))
        with opener.open(url + "/api/bootstrap", timeout=0.5) as response:
            if response.status != 200:
                return None
            content = response.read(16385)
        if len(content) > 16384:
            return None
        value = json.loads(content)
        runtime = value.get("runtime") if isinstance(value, dict) else None
        if (
            isinstance(value, dict)
            and value.get("local_only") is True
            and "runtime" not in value
            and " ".join("".join(title.parts).split()) == "Vesper & Co · Bank Rec"
        ):
            # The original public source build has no runtime identity or data
            # path. It may own this directory, so do not start a second writer.
            return LEGACY_UNVERIFIED
        if (
            value.get("local_only") is not True
            or not isinstance(runtime, dict)
            or runtime.get("port") != port
            or not isinstance(runtime.get("instance_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", runtime["instance_id"])
            or not isinstance(runtime.get("data_dir"), str)
            or len(runtime["data_dir"]) > 4096
            or type(runtime.get("testing")) is not bool
        ):
            return None
        return runtime
    except (OSError, HTTPError, URLError, ValueError, TypeError, AttributeError, RecursionError):
        return None


def _verified_url(data_dir, port, *, testing, instance_id=None):
    runtime = _read_runtime(port)
    if (
        not runtime
        or runtime is LEGACY_UNVERIFIED
        or runtime.get("application") != "vesper-bank-rec"
        or runtime["data_dir"] != str(Path(data_dir).resolve())
        or runtime["testing"] is not testing
        or (instance_id is not None and runtime["instance_id"] != instance_id)
    ):
        return None
    return f"http://127.0.0.1:{port}"


def existing_instance_url(data_dir, *, testing=False, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        metadata = _read_metadata(data_dir)
        if metadata:
            url = _verified_url(data_dir, testing=testing, **metadata)
            if url:
                return url
        time.sleep(0.1)
    raise DesktopError(
        "Bank Rec is already starting or running for this data folder, but its browser address "
        "could not be verified. Wait a moment and open the app again. No second copy was started."
    )


def find_existing_instance(data_dir, ports, *, testing=False):
    """Reuse a verified app, or stop safely when an older copy owns the same data."""
    for candidate in dict.fromkeys(ports):
        runtime = _read_runtime(candidate)
        if runtime is LEGACY_UNVERIFIED:
            raise DesktopError(
                "An older Bank Rec copy is already open, and its data folder cannot be verified. "
                "Close its launcher with Ctrl+C, then open this app again. No second copy was started; "
                "your saved work is preserved."
            )
        if not runtime or runtime["data_dir"] != str(Path(data_dir).resolve()):
            continue
        if runtime.get("application") != "vesper-bank-rec":
            raise DesktopError(
                "An older Bank Rec copy is already using this data folder. Close its launcher "
                "with Ctrl+C, then open this app again. Your saved work is preserved."
            )
        if runtime["testing"] is not testing:
            raise DesktopError(
                "Bank Rec is already using this data folder in another mode. Close that copy "
                "before opening this one. Your saved work is preserved."
            )
        return f"http://127.0.0.1:{candidate}"
    return None


def _bind_socket(preferred_port):
    """Keep the chosen socket bound; never probe-close-rebind or stop another app."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", preferred_port))
        except OSError as exc:
            if preferred_port == 0 or exc.errno != errno.EADDRINUSE:
                raise
            sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        return sock
    except BaseException:
        sock.close()
        raise


def _open_browser(url):
    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        _notify_error("Bank Rec is running. Open " + url + " in your browser to use it.")


def _open_when_ready(server, runtime, data_dir, *, testing, stopped):
    deadline = time.monotonic() + 15
    while not stopped.wait(0.1) and time.monotonic() < deadline:
        if server.should_exit:
            return
        if server.started:
            url = _verified_url(data_dir, runtime["port"], testing=testing, instance_id=runtime["instance_id"])
            if url:
                _open_browser(url)
                return


def run_desktop(data_dir, *, port=DEFAULT_PORT, no_browser=False, testing=False):
    from .app import create_app
    import uvicorn

    data_dir = Path(data_dir).resolve()
    lock = InstanceLock(data_dir)
    if not lock.acquire():
        url = existing_instance_url(data_dir, testing=testing)
        if not no_browser:
            _open_browser(url)
        return 0
    sock, runtime, stopped = None, None, threading.Event()
    try:
        # Source launchers predating this desktop lock may still be running. Reuse
        # only an app whose bootstrap identity confirms the same data directory.
        candidates = [port, DEFAULT_PORT]
        previous = _read_metadata(data_dir)
        if previous:
            candidates.insert(0, previous["port"])
        url = find_existing_instance(data_dir, candidates, testing=testing)
        if url:
            if not no_browser:
                _open_browser(url)
            return 0
        sock = _bind_socket(port)
        actual_port = sock.getsockname()[1]
        app = create_app(data_dir=data_dir, port=actual_port, testing=testing)
        runtime = app.state.runtime
        config = uvicorn.Config(
            app, host="127.0.0.1", port=actual_port, loop="asyncio", http="h11", ws="none",
            lifespan="on", workers=1, reload=False, log_config=None, log_level="warning",
            access_log=False, proxy_headers=False, timeout_graceful_shutdown=None,
        )
        server = uvicorn.Server(config)
        app.state.desktop = True
        app.state.desktop_shutdown = lambda: setattr(server, "should_exit", True)
        write_instance_metadata(data_dir, runtime)
        if not no_browser:
            threading.Thread(
                target=_open_when_ready,
                args=(server, runtime, data_dir),
                kwargs={"testing": testing, "stopped": stopped},
                daemon=True,
            ).start()
        try:
            server.run(sockets=[sock])
        except SystemExit:
            raise DesktopError("Bank Rec could not start its local browser service. Open the app again.") from None
        if not server.started:
            raise DesktopError("Bank Rec could not start its local browser service. Open the app again.")
        return 0
    finally:
        stopped.set()
        if runtime is not None:
            clear_instance_metadata(data_dir, runtime["instance_id"])
        if sock is not None:
            sock.close()
        lock.close()


def _notify_error(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr, flush=True)
    if not getattr(sys, "frozen", False):
        return
    try:
        if sys.platform == "darwin":
            import ctypes

            core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            core.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            core.CFStringCreateWithCString.restype = ctypes.c_void_p
            core.CFRelease.argtypes = [ctypes.c_void_p]
            core.CFRelease.restype = None
            core.CFUserNotificationDisplayAlert.argtypes = [
                ctypes.c_double, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            core.CFUserNotificationDisplayAlert.restype = ctypes.c_int32
            strings = []
            try:
                strings = [
                    core.CFStringCreateWithCString(None, text.encode("utf-8"), 0x08000100)
                    for text in ("Vesper Bank Rec", message, "OK")
                ]
                if all(strings):
                    result = ctypes.c_ulong()
                    core.CFUserNotificationDisplayAlert(
                        0, 0, None, None, None, *strings, None, None, ctypes.byref(result)
                    )
            finally:
                for value in strings:
                    if value:
                        core.CFRelease(value)
        elif os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Vesper Bank Rec", 0x10)
    except (OSError, AttributeError):
        pass


def main(argv=None):
    from .app import default_data_dir

    parser = argparse.ArgumentParser(description="Open Vesper Bank Rec")
    parser.add_argument("--data-dir", type=Path, help="Use an explicit local data folder")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Preferred local port; 0 chooses a free port")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--testing", action="store_true", help="Isolated memory-credential verification; requires --data-dir")
    args = parser.parse_args(argv)
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("Choose port 0 or a port from 1024 through 65535.")
    if args.testing and args.data_dir is None:
        parser.error("Verification mode requires an explicit --data-dir.")
    try:
        return run_desktop(
            args.data_dir if args.data_dir is not None else default_data_dir(),
            port=args.port, no_browser=args.no_browser, testing=args.testing,
        )
    except KeyboardInterrupt:
        return 0
    except DesktopError as exc:
        _notify_error(str(exc))
        return 1
    except OSError:
        _notify_error(
            "Bank Rec could not open its local data folder or browser service. Check that the "
            "data folder is available and writable, then open the app again. Your saved work is preserved."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
