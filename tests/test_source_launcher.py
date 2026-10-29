"""Source-launcher TCP lifecycle checks without providers or credential stores."""

import os
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
import uvicorn

import run as source_launcher
from vesper_bank_rec import desktop


@pytest.fixture
def isolated_launcher(monkeypatch, tmp_path):
    # This test must never probe an existing Bank Rec instance or use its data.
    monkeypatch.setattr(desktop, "find_existing_instance", lambda *args, **kwargs: None)

    def create_app(*, data_dir, port, testing):
        runtime = {
            "application": "vesper-bank-rec", "instance_id": uuid4().hex,
            "port": port, "data_dir": str(data_dir), "testing": testing,
        }
        return SimpleNamespace(state=SimpleNamespace(runtime=runtime))

    monkeypatch.setattr(
        source_launcher, "importlib",
        SimpleNamespace(import_module=lambda name: SimpleNamespace(create_app=create_app)),
    )
    return SimpleNamespace(data_dir=tmp_path / "source-data", no_browser=True, testing=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX restart behavior; Windows uses exclusive binding")
def test_source_launcher_restarts_with_previous_client_still_open(isolated_launcher, monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        isolated_launcher.port = reservation.getsockname()[1]

    clients = []
    accepted = []

    class Server:
        def __init__(self, config):
            pass

        def run(self, *, sockets):
            listener = sockets[0]
            listener.settimeout(2)
            client = socket.create_connection(listener.getsockname(), timeout=2)
            clients.append(client)
            connection, _ = listener.accept()
            with connection:
                connection.sendall(b"synthetic-response")
            assert client.recv(64) == b"synthetic-response"
            assert client.recv(1) == b""
            accepted.append(listener.getsockname())
            # The browser-side socket deliberately remains open after server FIN.

    monkeypatch.setattr(uvicorn, "Server", Server)
    try:
        assert source_launcher.serve_source(isolated_launcher) == 0
        assert source_launcher.serve_source(isolated_launcher) == 0
        assert len(accepted) == 2
        assert accepted[0] == accepted[1] == ("127.0.0.1", isolated_launcher.port)
    finally:
        for client in clients:
            client.close()


def test_source_launcher_refuses_an_active_listener(isolated_launcher, monkeypatch):
    def unexpected_server(config):
        pytest.fail("An occupied port must not start the application server")

    monkeypatch.setattr(uvicorn, "Server", unexpected_server)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen(1)
        owner.settimeout(2)
        isolated_launcher.port = owner.getsockname()[1]
        with pytest.raises(SystemExit, match="port .* is in use.*No process was stopped"):
            source_launcher.serve_source(isolated_launcher)

        # Refusal leaves the original listener usable and does not steal its port.
        with socket.create_connection(owner.getsockname(), timeout=2) as client:
            connection, _ = owner.accept()
            with connection:
                connection.sendall(b"original-listener")
            assert client.recv(64) == b"original-listener"
