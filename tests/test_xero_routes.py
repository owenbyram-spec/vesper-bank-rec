"""Actual local API flow with synthetic OAuth transport and accounting evidence."""

from contextlib import nullcontext
from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from vesper_bank_rec.app import create_app
from vesper_bank_rec.connectors.xero import XeroError
from vesper_bank_rec.xero_routes import XERO_SCOPES


TENANT = "11111111-1111-4111-8111-111111111111"
ACCOUNT = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def connected_app(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app) as client:
        client.get("/")
        csrf = client.get("/api/bootstrap").json()["csrf_token"]
        client.headers.update({"Origin": "http://testserver", "X-Vesper-CSRF": csrf})
        client.post("/api/workspaces/new", json={"name": "Synthetic Xero client", "currency": "USD"})

        def refresh_scope():
            result = client.get("/api/state").json()
            client.headers["X-Vesper-Scope"] = json.dumps(result["scope"])
            return result

        refresh_scope()
        oauth_calls = []

        def token(request):
            oauth_calls.append(request)
            assert request.url.host == "identity.xero.com"
            return httpx.Response(200, json={
                "access_token": "synthetic-xero-access", "refresh_token": "synthetic-xero-refresh",
                "token_type": "Bearer", "expires_in": 1800, "scope": " ".join(XERO_SCOPES),
            })

        app.state.common.xero.oauth.client = httpx.Client(transport=httpx.MockTransport(token))
        yield client, app, refresh_scope, oauth_calls
        app.state.common.xero.oauth.client.close()


def authorize(client):
    result = client.post("/api/settings/xero/authorize", json={"client_id": "synthetic-client", "persist": False})
    assert result.status_code == 200, result.text
    query = parse_qs(urlsplit(result.json()["authorization_url"]).query)
    assert set(query["scope"][0].split()) == set(XERO_SCOPES)
    assert "secret" not in query
    response = client.get(
        "http://localhost:8765/api/xero/callback",
        params={"state": query["state"][0], "code": "synthetic-authorization-code"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    return result.json(), response


class SyntheticBooks:
    def __init__(self, settings, wid):
        self.settings, self.wid = settings, wid

    def organizations(self):
        if not self.settings.get(self.wid, "xero"):
            raise XeroError("Connect Xero first.")
        return [{"id": TENANT, "name": "Synthetic Xero company"}]

    def bind(self, tenant_id, *, scope_check=lambda: True, scope_lock=None):
        if tenant_id != TENANT:
            raise XeroError("Choose an authorized organisation.")
        company = {"id": TENANT, "name": "Synthetic Xero company", "currency": "USD"}
        with scope_lock or nullcontext():
            if not scope_check():
                raise XeroError("The workspace changed during confirmation.")
            self.settings.replace(self.wid, "xero", {**self.settings.get(self.wid, "xero"), "bound_company": company})
        return company

    def accounts(self):
        self._bound()
        return [{"id": ACCOUNT, "name": "Synthetic operating bank", "currency": "USD", "type": "BANK"}]

    def _bound(self):
        bundle = self.settings.get(self.wid, "xero")
        if not bundle or not bundle.get("bound_company"):
            raise XeroError("Confirm the Xero organisation first.")

    def bank_ledger(self, account_id, start_date, end_date):
        self._bound()
        assert (account_id, start_date, end_date) == (ACCOUNT, "2026-09-01", "2026-09-30")
        return {
            "opening_balance": "5000.00", "closing_balance": "5850.00", "currency": "USD",
            "rows": [
                {"id": "synthetic-receipt", "date": "2026-09-03", "description": "Customer receipt", "amount": "1000.00", "source_ids": ["receipt-id"]},
                {"id": "synthetic-software-a", "date": "2026-09-04", "description": "Software", "amount": "-75.00", "source_ids": ["software-a"]},
                {"id": "synthetic-software-b", "date": "2026-09-04", "description": "Software", "amount": "-75.00", "source_ids": ["software-b"]},
            ],
            "source": {"provider": "Xero", "complete": True, "tenant_id": TENANT,
                       "account_id": ACCOUNT, "row_count": 3, "digest": "synthetic-digest"},
        }


def test_authorization_confirmation_account_period_import_and_export(connected_app):
    client, app, refresh_scope, calls = connected_app
    xero = app.state.common.xero
    xero.factory = lambda wid: SyntheticBooks(app.state.common.settings, wid)
    before = deepcopy(refresh_scope()["workspace"])
    started, callback = authorize(client)
    assert callback.status_code == 200
    assert len(calls) == 1
    assert refresh_scope()["workspace"] == before  # Credentials do not edit finance.
    status = client.get("/api/settings/xero").json()
    assert status["xero"]["configured"] and not status["xero"]["bound"]
    assert status["xero"]["storage"] == "session-only"
    for secret in ("synthetic-xero-access", "synthetic-xero-refresh", "synthetic-authorization-code"):
        assert secret not in callback.text + json.dumps(status)
    assert client.get("/api/xero/accounts").status_code == 400
    assert client.get("/api/xero/organisations").json()["organisations"][0]["id"] == TENANT
    assert client.post("/api/xero/confirm", json={"tenant_id": "wrong"}).status_code == 400
    confirmed = client.post("/api/xero/confirm", json={"tenant_id": TENANT})
    assert confirmed.status_code == 200 and confirmed.json()["xero"]["bound"]
    assert client.get("/api/xero/accounts").json()["accounts"][0]["id"] == ACCOUNT
    assert refresh_scope()["workspace"] == before
    assert client.post("/api/sessions/new", json={
        "account_id": ACCOUNT, "account_label": "Synthetic operating bank",
        "period_start": "2026-09-01", "period_end": "2026-09-30",
        "scope": {"workspace_id": before["id"], "session_id": None, "input_version": None, "revision": before["version"]},
    }).status_code == 200
    selected = refresh_scope()
    result = client.post("/api/xero/sync", json={"account_id": ACCOUNT, "start_date": "2026-09-01", "end_date": "2026-09-30"})
    assert result.status_code == 200, result.text
    assert result.json()["result"]["rows"] == 3 and "Xero" in result.json()["result"]["message"]
    state = refresh_scope()
    assert state["session"]["input_version"] == selected["session"]["input_version"] + 1
    ledger = state["session"]["ledger"]["rows"]
    assert len(ledger) == 3 and len({row["id"] for row in ledger}) == 3
    assert [row["amount"] for row in ledger].count("-75.00") == 2
    assert len({row["external_id"] for row in ledger}) == 3
    assert state["session"]["ledger"]["provider_provenance"]["provider"] == "Xero"
    assert not state["session"]["bank"]  # No provider data became a bank statement.
    url = "/api/export/json?" + "&".join(f"{key}={value}" for key, value in {
        "workspace_id": state["workspace"]["id"], "session_id": state["session"]["id"],
        "input_version": state["session"]["input_version"], "revision": state["workspace"]["version"],
    }.items())
    exported = client.get(url)
    assert exported.status_code == 200 and "synthetic-xero-access" not in exported.text
    saved = deepcopy(state["workspace"])
    assert client.delete("/api/settings/xero").status_code == 200
    assert refresh_scope()["workspace"] == saved
    assert not client.get("/api/settings/xero").json()["xero"]["configured"]
    # The authorization URL carries state/PKCE only, never received tokens.
    assert "synthetic-xero-access" not in json.dumps(started)


def test_failed_callback_safe_status_and_no_finance_change(connected_app):
    client, app, refresh_scope, calls = connected_app
    before = deepcopy(refresh_scope()["workspace"])
    started = client.post("/api/settings/xero/authorize", json={"client_id": "synthetic", "persist": False}).json()
    state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
    result = client.get("http://localhost:8765/api/xero/callback", params={
        "state": state, "error": "access_denied", "error_description": "provider-secret-never-echo",
    })
    assert result.status_code == 400 and "provider-secret-never-echo" not in result.text
    assert not calls
    assert not client.get("/api/settings/xero").json()["xero"]["configured"]
    assert client.get("/api/settings/xero").json()["authorization"]["status"] == "failed"
    assert refresh_scope()["workspace"] == before


def test_sync_is_displayed_scope_bound_and_partial_failure_preserves_finance(connected_app):
    client, app, refresh_scope, _ = connected_app
    scope = refresh_scope()
    before = deepcopy(scope["workspace"])
    stale = {**scope["scope"], "version": 999}
    result = client.post("/api/xero/sync", json={}, headers={"X-Vesper-Scope": json.dumps(stale)})
    assert result.status_code == 409
    assert refresh_scope()["workspace"] == before
    assert client.post("/api/xero/sync", json={"url": "https://attacker.invalid"}).status_code == 400
    assert client.post("/api/settings/xero/authorize", json={"client_id": "synthetic", "persist": True}).status_code == 400


def test_authorization_headers_and_delete_require_normal_local_session(connected_app):
    client, _app, _refresh, _ = connected_app
    assert client.post("/api/settings/xero/authorize", json={"client_id": "synthetic"}, headers={"X-Vesper-CSRF": "wrong"}).status_code == 403
    assert client.delete("/api/settings/xero", headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert client.get("http://localhost:8765/api/settings/xero").status_code == 403


def test_xero_import_waits_for_other_accounting_refresh(connected_app):
    client, app, refresh_scope, _ = connected_app
    before = deepcopy(refresh_scope()["workspace"])
    with app.state.common.polling.run_lock:
        result = client.post("/api/xero/sync", json={})
    assert result.status_code == 409 and "already running" in result.json()["detail"]
    assert refresh_scope()["workspace"] == before
