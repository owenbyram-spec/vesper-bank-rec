"""The browser may stop only its own explicitly desktop-managed backend."""

from fastapi.testclient import TestClient
import asyncio
import json
import threading

import pytest

from vesper_bank_rec.app import create_app


def connect(app):
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    client.get("/")
    bootstrap = client.get("/api/bootstrap").json()
    client.headers.update({
        "Origin": "http://127.0.0.1:8765",
        "X-Vesper-CSRF": bootstrap["csrf_token"],
    })
    return client, bootstrap


def test_source_launch_has_no_desktop_quit_authority(tmp_path):
    app = create_app(tmp_path, testing=True)
    client, bootstrap = connect(app)
    assert bootstrap["desktop_mode"] is False
    assert app.state.runtime == bootstrap["runtime"]
    assert bootstrap["runtime"]["application"] == "vesper-bank-rec"
    assert client.post("/api/app/quit", json={}).status_code == 409


def test_desktop_quit_needs_local_session_origin_and_csrf_but_no_workspace(tmp_path):
    app = create_app(tmp_path, testing=True)
    stopped = []
    app.state.desktop = True
    app.state.desktop_shutdown = lambda: stopped.append(True)
    client, bootstrap = connect(app)
    assert bootstrap["desktop_mode"] is True
    assert bootstrap["workspace"] is None
    for overrides in (
        {"X-Vesper-CSRF": "invalid"},
        {"Origin": "https://attacker.invalid"},
        {"Sec-Fetch-Site": "cross-site"},
    ):
        assert client.post("/api/app/quit", json={}, headers=overrides).status_code == 403
    foreign = TestClient(app, base_url="http://127.0.0.1:8765")
    assert foreign.post("/api/app/quit", json={}).status_code == 403
    assert stopped == []
    response = client.post("/api/app/quit", json={})
    assert response.status_code == 200 and response.json()["ok"]
    assert stopped == [True]
    assert app.state.store.all() == []


def test_quit_refuses_a_running_accounting_refresh_without_releasing_ownership(tmp_path):
    app = create_app(tmp_path, testing=True)
    stopped, entered, release = [], threading.Event(), threading.Event()
    app.state.desktop = True
    app.state.desktop_shutdown = lambda: stopped.append(True)
    client, _ = connect(app)
    worker = threading.Thread(target=lambda: app.state.common.polling.run(
        {}, "synthetic", callback=lambda *_: (entered.set(), release.wait(2))
    ))
    worker.start()
    try:
        assert entered.wait(1)
        response = client.post("/api/app/quit", json={})
        assert response.status_code == 409 and "accounting refresh" in response.text
        assert stopped == [] and app.state.shutting_down is False
    finally:
        release.set()
        worker.join(2)
    assert client.post("/api/app/quit", json={}).status_code == 200
    assert stopped == [True]


@pytest.mark.parametrize("path,body", [
    ("/api/settings/model/test", {}),
    ("/api/assistant", {"message": "Explain the synthetic evidence."}),
])
def test_quit_refuses_provider_work_in_another_tab(tmp_path, path, body):
    app = create_app(tmp_path, testing=True)
    stopped, entered, release = [], threading.Event(), threading.Event()
    app.state.desktop = True
    app.state.desktop_shutdown = lambda: stopped.append(True)
    client, _ = connect(app)
    client.post("/api/demo", json={})
    scope = client.get("/api/state").json()["scope"]
    client.headers["X-Vesper-Scope"] = json.dumps(scope)
    app.state.common.settings.set_model(scope["id"], {"api_key": "synthetic", "model": "synthetic", "consent": True})

    class WaitingAdapter:
        def respond(self, *_):
            entered.set()
            release.wait(2)
            return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "connected"}]}]}

    app.state.common.adapter_factory = lambda _: WaitingAdapter()
    worker = threading.Thread(target=lambda: client.post(path, json=body))
    worker.start()
    try:
        assert entered.wait(1)
        assert client.post("/api/app/quit", json={}).status_code == 409
        assert stopped == [] and app.state.shutting_down is False
    finally:
        release.set()
        worker.join(2)
    assert client.post("/api/app/quit", json={}).status_code == 200
    assert stopped == [True]


def test_accepted_shutdown_blocks_later_financial_writes(tmp_path):
    app = create_app(tmp_path, testing=True)
    app.state.desktop = True
    app.state.desktop_shutdown = lambda: None
    client, _ = connect(app)
    assert client.post("/api/app/quit", json={}).status_code == 200
    response = client.post("/api/workspaces/new", json={"name": "Must not be created"})
    assert response.status_code == 409 and "closing" in response.text
    assert app.state.store.all() == []


def test_lifespan_drains_background_refresh_before_returning_to_desktop_lock_owner(tmp_path):
    app = create_app(tmp_path, testing=True)
    entered, release = threading.Event(), threading.Event()

    async def scenario():
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        worker = threading.Thread(target=lambda: app.state.common.polling.run(
            {}, "synthetic", callback=lambda *_: (entered.set(), release.wait(2))
        ))
        worker.start()
        assert entered.wait(1)
        closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await asyncio.sleep(0.02)
            assert app.state.shutting_down and not closing.done()
        finally:
            release.set()
            worker.join(2)
        await asyncio.wait_for(closing, timeout=2)

    asyncio.run(scenario())
