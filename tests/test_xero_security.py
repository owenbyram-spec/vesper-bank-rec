"""Xero callback authority and actual Store-lock boundaries, using synthetic HTTP only."""

import json
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi.testclient import TestClient
import httpx
import pytest

from vesper_bank_rec.app import create_app
from vesper_bank_rec.connectors.xero import XeroClient
from vesper_bank_rec.xero_routes import XERO_SCOPES


ACCESS, REFRESH = "synthetic-xero-access-never-live", "synthetic-xero-refresh-never-live"
TENANT = "11111111-1111-4111-8111-111111111111"
CONNECTION = "22222222-2222-4222-8222-222222222222"


def refresh_scope(client):
    state = client.get("/api/state").json()
    client.headers["X-Vesper-Scope"] = json.dumps(state["scope"])
    return state


@pytest.fixture
def connected_app(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.get("/")
        bootstrap = client.get("/api/bootstrap").json()
        client.headers.update({"Origin": "http://127.0.0.1:8765", "X-Vesper-CSRF": bootstrap["csrf_token"]})
        assert client.post("/api/demo", json={}).status_code == 200
        refresh_scope(client)
        requests = []

        def provider(request):
            requests.append(request)
            if str(request.url) == "https://identity.xero.com/connect/token":
                return httpx.Response(200, json={
                    "access_token": ACCESS, "refresh_token": REFRESH, "token_type": "Bearer",
                    "expires_in": 1800, "scope": " ".join(XERO_SCOPES),
                })
            if str(request.url) == "https://api.xero.com/connections":
                return httpx.Response(200, json=[{
                    "id": CONNECTION, "tenantId": TENANT, "tenantType": "ORGANISATION",
                    "tenantName": "Synthetic Xero organisation",
                }])
            if str(request.url) == "https://api.xero.com/api.xro/2.0/Organisation":
                return httpx.Response(200, json={"Status": "OK", "Organisations": [{
                    "OrganisationID": TENANT, "Name": "Synthetic Xero organisation", "BaseCurrency": "USD",
                }]})
            pytest.fail("Unexpected provider destination in isolated Xero security test")

        with httpx.Client(transport=httpx.MockTransport(provider)) as transport:
            xero = app.state.common.xero
            xero.oauth.client = transport
            xero.factory = lambda wid: XeroClient(app.state.common.settings, wid, client=transport, lock=xero.lock)
            yield app, client, requests


def authorize(client):
    refresh_scope(client)
    response = client.post("/api/settings/xero/authorize", json={"client_id": "synthetic-client-id", "persist": False})
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]


def callback(app, state, **changes):
    # Xero returns in a browser on localhost, without the 127.0.0.1 session cookie.
    anonymous = TestClient(app, base_url="http://localhost:8765")
    return anonymous.get("/api/xero/callback?" + urlencode({"state": state, "code": "synthetic-code", **changes}),
                         headers={"Origin": "https://login.xero.com", "Sec-Fetch-Site": "cross-site"})


def another_thread_can_take(lock):
    result = []

    def probe():
        acquired = lock.acquire(blocking=False)
        result.append(acquired)
        if acquired:
            lock.release()

    worker = threading.Thread(target=probe)
    worker.start()
    worker.join(1)
    assert not worker.is_alive()
    return result == [True]


def test_nonce_authorizes_only_narrow_localhost_callback_without_browser_session(connected_app):
    app, client, requests = connected_app
    state = authorize(client)
    rejected = callback(app, "x" * 43)
    assert rejected.status_code == 400 and requests == []
    result = callback(app, state)
    assert result.status_code == 200
    assert len(requests) == 1
    for secret in (ACCESS, REFRESH, state, "synthetic-code"):
        assert secret not in result.text and secret not in str(dict(result.headers))
    public = client.get("/api/settings/xero").json()
    assert public["authorization"]["status"] == "connected"
    assert public["xero"]["configured"] and not public["xero"]["bound"]
    assert ACCESS not in json.dumps(public) and REFRESH not in json.dumps(public)
    assert callback(app, state).status_code == 400
    assert len(requests) == 1


@pytest.mark.parametrize("method,base,path,headers", [
    ("get", "http://localhost:8765", "/api/state", {}),
    ("post", "http://localhost:8765", "/api/xero/callback?state=synthetic-state&code=synthetic-code", {}),
    ("get", "http://127.0.0.1:8765", "/api/xero/callback?state=synthetic-state&code=synthetic-code", {"Origin": "https://login.xero.com"}),
    ("get", "http://127.0.0.1:8765", "/api/state", {"Origin": "https://login.xero.com"}),
])
def test_oauth_exception_does_not_authorize_other_hosts_methods_or_regular_apis(connected_app, method, base, path, headers):
    app, _, requests = connected_app
    foreign = TestClient(app, base_url=base)
    response = getattr(foreign, method)(path, headers=headers)
    assert response.status_code == 403
    assert requests == []


def test_callback_query_is_redacted_from_outer_asgi_scope_including_errors(connected_app):
    app, _, requests = connected_app
    observed = []

    async def observe(scope, receive, send):
        async def observe_send(message):
            if message["type"] == "http.response.start":
                observed.append(scope.get("query_string"))
            await send(message)
        await app(scope, receive, observe_send)

    browser = TestClient(observe, base_url="http://localhost:8765")
    response = browser.get("/api/xero/callback?state=" + "x" * 43 + "&code=synthetic-secret-code&code=duplicate")
    assert response.status_code == 400 and observed == [b""]
    assert "synthetic-secret-code" not in response.text and requests == []
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize("closing,method", [(False, "post"), (True, "get")])
def test_denied_and_closing_callbacks_also_receive_private_security_headers(connected_app, closing, method):
    app, _, requests = connected_app
    app.state.shutting_down = closing
    browser = TestClient(app, base_url="http://localhost:8765")
    response = getattr(browser, method)("/api/xero/callback?state=" + "x" * 43 + "&code=synthetic-secret-code")
    assert response.status_code == (409 if closing else 403)
    assert "synthetic-secret-code" not in response.text and requests == []
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_workspace_round_trip_cannot_restore_authority_to_an_old_oauth_callback(connected_app):
    app, client, requests = connected_app
    original = refresh_scope(client)
    state = authorize(client)
    other = app.state.store.create("Other synthetic client", "USD", True)
    app.state.common.settings.register_new_workspace(other["id"])
    app.state.store.select(original["workspace"]["id"])
    for wid in (other["id"], original["workspace"]["id"]):
        assert client.post("/api/workspaces/select", json={"workspace_id": wid}).status_code == 200
        refresh_scope(client)
    assert callback(app, state).status_code == 400
    assert requests == []
    assert app.state.common.settings.get(original["workspace"]["id"], "xero") is None
    assert refresh_scope(client)["workspace"] == original["workspace"]


def test_authorization_capture_and_callback_token_save_hold_the_actual_finance_store_lock(connected_app, monkeypatch):
    app, client, _ = connected_app
    xero, store = app.state.common.xero, app.state.store
    snapshot, save = xero.snapshot, app.state.common.settings.save
    observations = []

    def scoped_snapshot():
        observations.append(("capture", another_thread_can_take(store.lock)))
        return snapshot()

    monkeypatch.setattr(xero, "snapshot", scoped_snapshot)
    state = authorize(client)
    assert observations and all(not acquired for _, acquired in observations)
    monkeypatch.setattr(xero, "snapshot", snapshot)

    def scoped_save(wid, kind, bundle, persist=False):
        if kind == "xero":
            observations.append(("save", another_thread_can_take(store.lock)))
        return save(wid, kind, bundle, persist)

    monkeypatch.setattr(app.state.common.settings, "save", scoped_save)
    assert callback(app, state).status_code == 200
    assert ("save", False) in observations
    assert not any(acquired for _, acquired in observations)


def test_organisation_binding_write_holds_store_lock_and_preserves_financial_records(connected_app, monkeypatch):
    app, client, _ = connected_app
    before = refresh_scope(client)
    assert callback(app, authorize(client)).status_code == 200
    replace, locked = app.state.common.settings.replace, []

    def replace_under_scope(wid, kind, bundle):
        if kind == "xero" and bundle.get("bound_company"):
            locked.append(not another_thread_can_take(app.state.store.lock))
        return replace(wid, kind, bundle)

    monkeypatch.setattr(app.state.common.settings, "replace", replace_under_scope)
    response = client.post("/api/xero/confirm", json={"tenant_id": TENANT})
    assert response.status_code == 200, response.text
    assert locked == [True]
    assert response.json()["xero"]["bound"]
    assert refresh_scope(client)["workspace"] == before["workspace"]


def test_confirm_request_waiting_for_connection_lock_cannot_bind_after_workspace_changes(connected_app):
    app, client, requests = connected_app
    original = refresh_scope(client)
    assert callback(app, authorize(client)).status_code == 200
    requests.clear()
    other = app.state.store.create("Other synthetic client", "USD", True)
    app.state.common.settings.register_new_workspace(other["id"])
    app.state.store.select(original["workspace"]["id"])
    xero, responses = app.state.common.xero, []
    with xero.lock:
        worker = threading.Thread(target=lambda: responses.append(client.post("/api/xero/confirm", json={"tenant_id": TENANT})))
        worker.start()
        deadline = time.monotonic() + 2
        while xero.active == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert xero.active > 0
        assert client.post("/api/workspaces/select", json={"workspace_id": other["id"]}).status_code == 200
    worker.join(2)
    assert not worker.is_alive() and responses[0].status_code == 409
    assert requests == []
    bundle = app.state.common.settings.get(original["workspace"]["id"], "xero")
    assert bundle and "bound_company" not in bundle
