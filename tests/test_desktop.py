"""Isolated desktop startup, ownership and loopback checks; no provider or OS secrets."""

from contextlib import contextmanager
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

import pytest

import run as source_launcher
from vesper_bank_rec import desktop


INSTANCE_ID = "a" * 32
ROOT = Path(__file__).resolve().parent.parent


def runtime(data_dir, port=9876, **changes):
    return {
        "application": "vesper-bank-rec", "instance_id": INSTANCE_ID, "port": port,
        "data_dir": str(Path(data_dir).resolve()), "testing": True, **changes,
    }


def test_advisory_lock_uses_resolved_directory_and_releases_without_unlinking(tmp_path):
    folder = tmp_path / "data"
    owner, second = desktop.InstanceLock(folder), desktop.InstanceLock(folder / ".." / "data")
    try:
        assert owner.acquire() is True
        assert second.acquire() is False
        with pytest.raises(desktop.DesktopError, match="already holds"):
            owner.acquire()
        lock_path = folder / desktop.LOCK_NAME
        assert lock_path.exists()
        owner.close()
        assert lock_path.exists()
        assert second.acquire() is True
    finally:
        owner.close()
        second.close()
        second.close()


def test_metadata_is_atomic_private_and_contains_only_public_identity(tmp_path):
    identity = runtime(tmp_path, api_key="synthetic-key-never-save")
    desktop.write_instance_metadata(tmp_path, identity)
    metadata_path = tmp_path / desktop.METADATA_NAME
    assert desktop._read_metadata(tmp_path) == {"port": 9876, "instance_id": INSTANCE_ID}
    assert "synthetic-key-never-save" not in metadata_path.read_text()
    assert len(list(tmp_path.iterdir())) == 1
    if os.name != "nt":
        assert stat.S_IMODE(metadata_path.stat().st_mode) == 0o600
    desktop.clear_instance_metadata(tmp_path, "b" * 32)
    assert metadata_path.exists(), "Cleanup must not remove another instance's receipt"
    desktop.clear_instance_metadata(tmp_path, INSTANCE_ID)
    assert not metadata_path.exists()


@pytest.mark.parametrize("content", [
    "not-json", "[]", '{"port":true,"instance_id":"' + INSTANCE_ID + '"}',
    json.dumps({"port": 9876, "instance_id": INSTANCE_ID, "unexpected": "synthetic-value"}),
    json.dumps({"port": 9876, "instance_id": "not-an-instance"}),
    json.dumps({"port": 80, "instance_id": INSTANCE_ID}),
    json.dumps({"port": 65536, "instance_id": INSTANCE_ID}),
    json.dumps({"port": "9876", "instance_id": INSTANCE_ID}),
    "x" * 1025,
])
def test_invalid_or_oversized_instance_receipt_is_not_trusted(tmp_path, content):
    (tmp_path / desktop.METADATA_NAME).write_text(content)
    assert desktop._read_metadata(tmp_path) is None


@contextmanager
def foreign_server(response_factory):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            status, headers, body = response_factory(self.path, self.server.server_port)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_identity_probe_does_not_follow_redirects_or_use_ambient_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with foreign_server(lambda path, port: (302, {"Location": "https://example.invalid/"}, b"")) as (port, requests):
        assert desktop._read_runtime(port) is None
        assert requests == ["/"]


def test_occupied_foreign_http_port_does_not_qualify_as_this_application(tmp_path):
    def foreign(path, port):
        body = b"Foreign application" if path == "/" else json.dumps({
            "local_only": True, "runtime": runtime(tmp_path / "other-data", port),
        }).encode()
        return 200, {}, body

    with foreign_server(foreign) as (port, requests):
        assert desktop.find_existing_instance(tmp_path, [port], testing=True) is None
        assert requests == ["/", "/api/bootstrap"]
        sock = desktop._bind_socket(port)
        try:
            assert sock.getsockname()[0] == "127.0.0.1"
            assert sock.getsockname()[1] != port
            assert desktop._read_runtime(port)["data_dir"] == str((tmp_path / "other-data").resolve())
        finally:
            sock.close()


@pytest.mark.parametrize("title", ["Vesper & Co · Bank Rec", "Vesper &amp; Co · Bank Rec"])
def test_original_public_app_without_runtime_blocks_both_launchers_safely(tmp_path, monkeypatch, title):
    def legacy(path, port):
        body = ("<!doctype html><html><head><title>" + title + "</title></head></html>").encode()
        if path == "/api/bootstrap":
            body = json.dumps({"local_only": True, "csrf_token": "synthetic-session-token", "workspace": None}).encode()
        return 200, {}, body

    def unexpected_bind(*args, **kwargs):
        pytest.fail("An unverified older app must be resolved before binding another data writer")

    monkeypatch.setattr(desktop, "_bind_socket", unexpected_bind)
    with foreign_server(legacy) as (port, requests):
        assert desktop._read_runtime(port) is desktop.LEGACY_UNVERIFIED
        assert desktop._verified_url(tmp_path, port, testing=True, instance_id=INSTANCE_ID) is None
        with pytest.raises(desktop.DesktopError, match="data folder cannot be verified"):
            desktop.run_desktop(tmp_path, port=port, no_browser=True, testing=True)
        args = SimpleNamespace(data_dir=tmp_path, port=port, no_browser=True, testing=True)
        with pytest.raises(desktop.DesktopError, match="data folder cannot be verified"):
            source_launcher.serve_source(args)
        assert not (tmp_path / "bank-rec.sqlite3").exists()
        assert set(requests) == {"/", "/api/bootstrap"}
    lock = desktop.InstanceLock(tmp_path)
    try:
        assert lock.acquire(), "Both rejected launch attempts must release their ownership lock"
    finally:
        lock.close()


@pytest.mark.parametrize("title", ["Vesper & Co · Cash Flow", "Another local application"])
def test_other_application_without_runtime_does_not_trigger_bank_rec_legacy_block(tmp_path, title):
    def other(path, port):
        body = ("<title>" + title + "</title>").encode() if path == "/" else json.dumps({"local_only": True}).encode()
        return 200, {}, body

    with foreign_server(other) as (port, _):
        assert desktop._read_runtime(port) is None
        assert desktop.find_existing_instance(tmp_path, [port], testing=True) is None


@pytest.mark.parametrize("changes", [
    {"application": "other-app"}, {"application": None}, {"data_dir": "/different-data"},
    {"testing": False}, {"instance_id": "b" * 32},
])
def test_relaunch_checks_product_directory_mode_and_exact_instance(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(desktop, "_read_runtime", lambda port: {**runtime(tmp_path, port), **changes})
    assert desktop._verified_url(tmp_path, 9876, testing=True, instance_id=INSTANCE_ID) is None


def test_verified_existing_app_is_reused_without_opening_an_unverified_older_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_read_runtime", lambda port: runtime(tmp_path, port))
    assert desktop.find_existing_instance(tmp_path, [9876], testing=True) == "http://127.0.0.1:9876"
    monkeypatch.setattr(desktop, "_read_runtime", lambda port: runtime(tmp_path, port, application=None))
    with pytest.raises(desktop.DesktopError, match="older Bank Rec copy"):
        desktop.find_existing_instance(tmp_path, [9876], testing=True)
    monkeypatch.setattr(desktop, "_read_runtime", lambda port: runtime(tmp_path, port, testing=False))
    with pytest.raises(desktop.DesktopError, match="another mode"):
        desktop.find_existing_instance(tmp_path, [9876], testing=True)


def test_stale_or_unverified_metadata_never_opens_a_foreign_port(tmp_path, monkeypatch):
    desktop.write_instance_metadata(tmp_path, runtime(tmp_path))
    monkeypatch.setattr(desktop, "_read_runtime", lambda port: runtime(tmp_path, port, instance_id="b" * 32))
    with pytest.raises(desktop.DesktopError, match="No second copy was started"):
        desktop.existing_instance_url(tmp_path, testing=True, timeout=0.02)


def test_bound_socket_selects_free_loopback_port_and_keeps_it_reserved():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        selected = desktop._bind_socket(port)
        try:
            actual = selected.getsockname()
            assert actual[0] == "127.0.0.1" and actual[1] != port
            with socket.socket() as competitor:
                with pytest.raises(OSError):
                    competitor.bind(actual)
            assert occupied.getsockname()[1] == port
        finally:
            selected.close()


@pytest.mark.parametrize("args", [["--testing"], ["--port", "80"], ["--port", "65536"], ["--port", "-1"]])
def test_invalid_cli_arguments_cannot_start_a_server(args):
    with pytest.raises(SystemExit) as error:
        desktop.main(args)
    assert error.value.code == 2


def test_startup_filesystem_error_has_safe_message_without_raw_exception(tmp_path, monkeypatch):
    displayed = []

    def fail(*args, **kwargs):
        raise OSError("synthetic-secret-error-detail")

    monkeypatch.setattr(desktop, "run_desktop", fail)
    monkeypatch.setattr(desktop, "_notify_error", displayed.append)
    assert desktop.main(["--testing", "--data-dir", str(tmp_path), "--no-browser"]) == 1
    assert len(displayed) == 1 and "synthetic-secret-error-detail" not in displayed[0]
    assert "saved work is preserved" in displayed[0]


def test_desktop_waits_for_server_drain_before_releasing_data_ownership(tmp_path, monkeypatch):
    app = SimpleNamespace(state=SimpleNamespace(runtime=runtime(tmp_path)))
    selected_socket = SimpleNamespace(getsockname=lambda: ("127.0.0.1", 9876), close=lambda: None)
    monkeypatch.setattr(desktop, "find_existing_instance", lambda *args, **kwargs: None)
    monkeypatch.setattr(desktop, "_bind_socket", lambda port: selected_socket)
    monkeypatch.setattr("vesper_bank_rec.app.create_app", lambda **kwargs: app)
    configuration = {}

    def config(application, **options):
        assert application is app
        configuration.update(options)
        return SimpleNamespace(**options)

    class Server:
        def __init__(self, config):
            self.started, self.should_exit = True, False

        def run(self, sockets):
            assert sockets == [selected_socket]
            app.state.desktop_shutdown()
            assert self.should_exit
            # Setting the exit flag must not release ownership while a request
            # is still draining inside Server.run(). There must be no ASGI-task
            # cancellation deadline that leaves a worker writing after return.
            contender = desktop.InstanceLock(tmp_path)
            try:
                assert contender.acquire() is False
                assert desktop._read_metadata(tmp_path)["instance_id"] == INSTANCE_ID
            finally:
                contender.close()

    monkeypatch.setattr("uvicorn.Config", config)
    monkeypatch.setattr("uvicorn.Server", Server)
    assert desktop.run_desktop(tmp_path, port=9876, no_browser=True, testing=True) == 0
    assert configuration["timeout_graceful_shutdown"] is None
    assert configuration["proxy_headers"] is False and configuration["access_log"] is False
    assert desktop._read_metadata(tmp_path) is None
    contender = desktop.InstanceLock(tmp_path)
    try:
        assert contender.acquire() is True
    finally:
        contender.close()


def command(data_dir, port=0):
    return [sys.executable, "-m", "vesper_bank_rec.desktop", "--testing", "--no-browser",
            "--data-dir", str(data_dir), "--port", str(port)]


@contextmanager
def running_desktop(data_dir, port=0):
    process = subprocess.Popen(command(data_dir, port), cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            metadata = desktop._read_metadata(data_dir)
            if metadata and desktop._verified_url(data_dir, testing=True, **metadata):
                yield process, metadata
                return
            if process.poll() is not None:
                pytest.fail("Isolated desktop startup failed: " + process.communicate()[1])
            time.sleep(0.05)
        pytest.fail("Isolated desktop did not become ready")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.communicate()


def api_client(port):
    url = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    with opener.open(url + "/", timeout=2):
        pass
    with opener.open(url + "/api/bootstrap", timeout=2) as response:
        bootstrap = json.load(response)

    def request(path, body=None):
        headers = {"Origin": url, "X-Vesper-CSRF": bootstrap["csrf_token"], "Content-Type": "application/json"}
        data = None if body is None else json.dumps(body).encode()
        with opener.open(Request(url + path, data=data, headers=headers), timeout=3) as response:
            return json.load(response)

    return bootstrap, request


def test_real_desktop_relaunches_same_instance_and_quits_without_losing_finance(tmp_path):
    data_dir = tmp_path / "isolated-desktop-data"
    with running_desktop(data_dir) as (owner, metadata):
        bootstrap, request = api_client(metadata["port"])
        assert bootstrap["desktop_mode"] is True and bootstrap["runtime"]["testing"] is True
        assert bootstrap["runtime"]["data_dir"] == str(data_dir.resolve())
        request("/api/demo", {})
        before = request("/api/state")["workspace"]
        second = subprocess.run(command(data_dir), cwd=ROOT, capture_output=True, text=True, timeout=15)
        assert second.returncode == 0, second.stderr
        assert owner.poll() is None
        assert desktop._read_metadata(data_dir) == metadata
        assert request("/api/state")["workspace"] == before
        assert request("/api/app/quit", {})["ok"] is True
        assert owner.wait(timeout=10) == 0
    assert desktop._read_metadata(data_dir) is None
    with running_desktop(data_dir) as (restarted, next_metadata):
        assert next_metadata["instance_id"] != metadata["instance_id"]
        _, request = api_client(next_metadata["port"])
        assert request("/api/state")["workspace"] == before
        assert request("/api/app/quit", {})["ok"] is True
        assert restarted.wait(timeout=10) == 0
    assert (data_dir / desktop.LOCK_NAME).exists()
