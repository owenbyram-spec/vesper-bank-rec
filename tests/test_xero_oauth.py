"""Synthetic PKCE callbacks and HTTP fixtures; no Xero account, files, or keychain."""

import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from vesper_bank_rec.connectors.xero_oauth import (
    AUTH_LIFETIME, AUTHORIZE_URL, TOKEN_URL, XeroOAuth, XeroOAuthError, parse_callback,
)


INSTANCE = "a" * 32
CLIENT = "synthetic-client-id"
REDIRECT = "http://localhost:8765/api/xero/callback"
SCOPES = "offline_access accounting.settings.read accounting.banktransactions.read accounting.payments.read accounting.reports.banksummary.read"
ACCESS, REFRESH = "synthetic-access-token-never-live", "synthetic-refresh-token-never-live"


def scope(**changes):
    return {
        "id": "workspace-synthetic", "synthetic": True, "currency": "USD", "version": 20,
        "session_id": "session-synthetic", "account_id": "SYNTH-CHECKING", "input_version": 4,
        "period_start": "2026-09-01", "period_end": "2026-09-30",
        "instance_id": INSTANCE, "xero_generation": 0, **changes,
    }


class Clock:
    now = 100000.0

    def __call__(self):
        return self.now


def tokens(**changes):
    return {"access_token": ACCESS, "refresh_token": REFRESH, "token_type": "Bearer", "expires_in": 1800, **changes}


def attempt(oauth, captured=None, **changes):
    public = oauth.begin(CLIENT, REDIRECT, SCOPES, scope() if captured is None else captured, **changes)
    query = parse_qs(urlsplit(public["authorization_url"]).query)
    return public, query, query["state"][0]


def test_pkce_exchange_is_scoped_single_use_and_backend_only():
    clock, requests = Clock(), []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, json=tokens(scope=" ".join(reversed(SCOPES.split()))))

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        oauth = XeroOAuth(instance_id=INSTANCE, client=client, clock=clock)
        captured = scope()
        public, query, state = attempt(oauth, captured, persist=True)
        assert urlsplit(public["authorization_url"])._replace(query="").geturl() == AUTHORIZE_URL
        assert query["code_challenge_method"] == ["S256"] and query["response_type"] == ["code"]
        assert query["redirect_uri"] == [REDIRECT] and query["client_id"] == [CLIENT]
        assert set(query["scope"][0].split()) == set(SCOPES.split())
        assert re.fullmatch(r"[A-Za-z0-9_-]{43,128}", state)
        assert public["expires_at"] == clock.now + AUTH_LIFETIME
        result = oauth.finish(state=state, code="synthetic-code", current_scope=lambda: captured)
        assert result["captured_scope"] == captured and result["captured_scope"] is not captured
        assert result["persist"] is True and result["authorization_id"] == public["authorization_id"]
        assert result["bundle"] == {
            "access_token": ACCESS, "refresh_token": REFRESH, "client_id": CLIENT,
            "expires_at": clock.now + 1800, "scope": " ".join(sorted(SCOPES.split())),
        }
        assert ACCESS not in json.dumps(public) and REFRESH not in json.dumps(public)
        assert len(requests) == 1
        request = requests[0]
        assert str(request.url) == TOKEN_URL
        body = parse_qs(request.content.decode())
        assert set(body) == {"grant_type", "client_id", "code", "redirect_uri", "code_verifier"}
        assert body["grant_type"] == ["authorization_code"] and body["code"] == ["synthetic-code"]
        assert body["redirect_uri"] == [REDIRECT] and body["client_id"] == [CLIENT]
        verifier = body["code_verifier"][0]
        assert re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert query["code_challenge"] == [challenge] and verifier not in json.dumps(public)
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert "Authorization" not in request.headers
        with pytest.raises(XeroOAuthError, match="invalid or expired"):
            oauth.finish(state=state, code="synthetic-code", current_scope=captured)
        assert len(requests) == 1


@pytest.mark.parametrize("field,value", [
    ("id", "workspace-other"), ("version", 21), ("session_id", "session-other"),
    ("input_version", 5), ("account_id", "OTHER"), ("period_start", "2026-08-01"),
    ("instance_id", "b" * 32), ("xero_generation", 1), ("version", True),
])
def test_changed_scope_or_settings_refuses_callback_before_provider(field, value):
    def forbidden(request):
        pytest.fail("Changed scope must never reach Xero's token endpoint")

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        oauth = XeroOAuth(instance_id=INSTANCE, client=client)
        _, _, state = attempt(oauth)
        with pytest.raises(XeroOAuthError, match="changed"):
            oauth.finish(state=state, code="synthetic-code", current_scope=scope(**{field: value}))
        with pytest.raises(XeroOAuthError, match="invalid or expired"):
            oauth.finish(state=state, code="synthetic-code", current_scope=scope())


def test_expiry_denial_disconnect_restart_and_superseded_begin_are_single_use():
    clock = Clock()
    oauth = XeroOAuth(instance_id=INSTANCE, clock=clock)
    _, _, expired = attempt(oauth)
    clock.now += AUTH_LIFETIME
    with pytest.raises(XeroOAuthError, match="expired"):
        oauth.finish(state=expired, code="synthetic-code", current_scope=scope())
    _, _, denied = attempt(oauth)
    with pytest.raises(XeroOAuthError, match="authorization was not completed") as error:
        oauth.finish(state=denied, error="synthetic-provider-description", current_scope=scope())
    assert "synthetic-provider-description" not in str(error.value)
    with pytest.raises(XeroOAuthError, match="invalid or expired"):
        oauth.finish(state=denied, code="synthetic-code", current_scope=scope())
    _, _, disconnected = attempt(oauth)
    oauth.invalidate(scope()["id"])
    with pytest.raises(XeroOAuthError, match="invalid or expired"):
        oauth.finish(state=disconnected, code="synthetic-code", current_scope=scope())
    _, _, restarted = attempt(oauth)
    with pytest.raises(XeroOAuthError, match="invalid or expired"):
        XeroOAuth(instance_id=INSTANCE).finish(state=restarted, code="synthetic-code", current_scope=scope())
    _, query, _ = attempt(oauth)
    assert query["state"][0] != restarted
    with pytest.raises(XeroOAuthError, match="invalid or expired"):
        oauth.finish(state=restarted, code="synthetic-code", current_scope=scope())


@pytest.mark.parametrize("change", ["workspace", "settings", "disconnect", "new-begin", "expiry"])
def test_change_during_exchange_cannot_authorize_late_tokens(change):
    clock, current = Clock(), scope()
    oauth = None

    def provider(request):
        if change == "workspace":
            current["id"] = "other-workspace"
        elif change == "settings":
            current["xero_generation"] += 1
        elif change == "disconnect":
            oauth.invalidate(current["id"])
        elif change == "new-begin":
            attempt(oauth)
        else:
            clock.now += AUTH_LIFETIME
        return httpx.Response(200, json=tokens())

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        oauth = XeroOAuth(instance_id=INSTANCE, client=client, clock=clock)
        _, _, state = attempt(oauth)
        with pytest.raises(XeroOAuthError, match="changed during connection") as error:
            oauth.finish(state=state, code="synthetic-code", current_scope=lambda: current)
        assert ACCESS not in str(error.value) and REFRESH not in str(error.value)


def test_callback_reentry_cannot_exchange_same_state_twice():
    requests, rejected = [], []
    oauth = state = None

    def provider(request):
        requests.append(request)
        with pytest.raises(XeroOAuthError, match="invalid or expired"):
            oauth.finish(state=state, code="synthetic-code", current_scope=scope())
        rejected.append(True)
        return httpx.Response(200, json=tokens())

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        oauth = XeroOAuth(instance_id=INSTANCE, client=client)
        _, _, state = attempt(oauth)
        assert oauth.finish(state=state, code="synthetic-code", current_scope=scope())["bundle"]["access_token"] == ACCESS
    assert len(requests) == 1 and rejected == [True]


@pytest.mark.parametrize("pairs", [
    [("state", "s" * 43), ("state", "s" * 43), ("code", "synthetic-code")],
    [("state", "s" * 43), ("code", "first"), ("code", "second")],
    [("state", "s" * 43), ("code", "synthetic-code"), ("error", "access_denied")],
    [("state", "s" * 43)], [("code", "synthetic-code")],
    [("state", "s" * 129), ("code", "synthetic-code")],
    [("state", "s" * 43), ("code", "x" * 4097)],
    [("state", "s" * 43), ("code", "code\r\nsynthetic-secret")],
    [("state", "s" * 43), ("code", "synthetic-code"), ("redirect_uri", "https://attacker.invalid")],
])
def test_duplicate_ambiguous_or_oversized_callback_values_fail_closed(pairs):
    with pytest.raises(XeroOAuthError) as error:
        parse_callback(pairs)
    assert "synthetic-secret" not in str(error.value)


def test_denial_parser_ignores_untrusted_provider_description():
    result = parse_callback([("state", "s" * 43), ("error", "access_denied"), ("error_description", "synthetic-secret")])
    assert result == {"state": "s" * 43, "code": None, "error": "access_denied"}


@pytest.mark.parametrize("redirect", [
    "https://attacker.invalid/api/xero/callback", "http://127.0.0.1:8765/api/xero/callback",
    "http://localhost:8765/wrong", "http://localhost:8765/api/xero/callback?code=other",
    "http://localhost:8765/api/xero/callback#fragment", "http://user@localhost:8765/api/xero/callback",
    "http://localhost/api/xero/callback", "http://localhost:80/api/xero/callback",
])
def test_begin_accepts_only_exact_registered_loopback_callback(redirect):
    with pytest.raises(XeroOAuthError, match="exact registered localhost"):
        XeroOAuth(instance_id=INSTANCE).begin(CLIENT, redirect, SCOPES, scope())


@pytest.mark.parametrize("changes", [
    {"client_id": "x" * 201}, {"persist": "true"}, {"scope": "accounting.settings.read"},
    {"scope": "offline_access offline_access"}, {"captured_scope": scope(instance_id="other-instance")},
    {"captured_scope": {"id": "workspace-only"}},
])
def test_begin_requires_valid_client_permissions_storage_and_instance_binding(changes):
    values = {"client_id": CLIENT, "redirect_uri": REDIRECT, "scope": SCOPES, "captured_scope": scope(), **changes}
    with pytest.raises(XeroOAuthError):
        XeroOAuth(instance_id=INSTANCE).begin(**values)


@pytest.mark.parametrize("payload", [
    tokens(refresh_token=None), tokens(access_token="bad\r\nheader"), tokens(expires_in=True),
    tokens(expires_in=1800.5), tokens(expires_in=0), tokens(token_type="Other"),
    tokens(scope="offline_access accounting.settings.read"), {"error": "synthetic-secret"}, ["synthetic-secret"],
])
def test_invalid_partial_or_wrong_permission_token_bundle_never_escapes(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        oauth = XeroOAuth(instance_id=INSTANCE, client=client)
        _, _, state = attempt(oauth)
        with pytest.raises(XeroOAuthError) as error:
            oauth.finish(state=state, code="synthetic-code", current_scope=scope())
        assert ACCESS not in str(error.value) and REFRESH not in str(error.value) and "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize("status,content", [
    (400, b'{"error":"synthetic-secret"}'), (429, b'{"error":"synthetic-secret"}'),
    (503, b'{"error":"synthetic-secret"}'), (200, b"not-json-synthetic-secret"),
    (200, (json.dumps(tokens())[:-1] + ',"access_token":"second"}').encode()),
    (200, b'{"unexpected":NaN}'), (200, b"x" * 128001),
])
def test_provider_failures_bad_json_and_duplicate_fields_are_static_and_single_use(status, content):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, content=content))) as client:
        oauth = XeroOAuth(instance_id=INSTANCE, client=client)
        _, _, state = attempt(oauth)
        with pytest.raises(XeroOAuthError, match="could not complete") as error:
            oauth.finish(state=state, code="synthetic-code", current_scope=scope())
        assert "synthetic-secret" not in str(error.value)
        with pytest.raises(XeroOAuthError, match="invalid or expired"):
            oauth.finish(state=state, code="synthetic-code", current_scope=scope())


def test_injected_redirect_enabled_client_is_still_forbidden_to_follow_token_redirect():
    requests = []

    def redirect(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://attacker.invalid/tokens"})

    with httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        oauth = XeroOAuth(instance_id=INSTANCE, client=client)
        _, _, state = attempt(oauth)
        with pytest.raises(XeroOAuthError):
            oauth.finish(state=state, code="synthetic-code", current_scope=scope())
    assert requests == [TOKEN_URL]


def test_owned_client_disables_environment_proxies_and_closes_on_transport_failure(monkeypatch):
    real_client, clients = httpx.Client, []

    def fail(request):
        raise httpx.ReadTimeout("synthetic-secret", request=request)

    def factory(**options):
        client = real_client(**options, transport=httpx.MockTransport(fail))
        clients.append((client, options))
        return client

    monkeypatch.setattr("vesper_bank_rec.connectors.xero_oauth.httpx.Client", factory)
    oauth = XeroOAuth(instance_id=INSTANCE)
    _, _, state = attempt(oauth)
    with pytest.raises(XeroOAuthError) as error:
        oauth.finish(state=state, code="synthetic-code", current_scope=scope())
    assert "synthetic-secret" not in str(error.value)
    assert clients[0][1] == {"timeout": 20, "follow_redirects": False, "trust_env": False}
    assert clients[0][0].is_closed
