"""Process-local Xero PKCE coordination; callers alone save tokens atomically.

The caller must pass its full non-secret scope, including instance_id and a
settings generation, then revalidate that scope under its storage lock before
saving finish()'s backend-only result. Nothing here reads credentials or files.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx


AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
AUTH_LIFETIME = 600
MAX_TOKEN_RESPONSE = 128000


class XeroOAuthError(ValueError):
    """Static safe errors only; never include provider content or callback values."""


def _scope_snapshot(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("id"), str)
            or not value["id"]
            or len(value["id"]) > 200
            or not isinstance(value.get("instance_id"), str)
            or type(value.get("xero_generation")) is not int or value["xero_generation"] < 0
            or len(encoded) > 8192
        ):
            raise ValueError()
        return encoded
    except (ValueError, TypeError, RecursionError):
        raise XeroOAuthError("Select the intended workspace before connecting Xero.") from None


def _scopes(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise XeroOAuthError("The requested Xero permissions are invalid. Start the connection again.")
    parts = value.split()
    if (
        not parts or len(parts) > 32 or len(set(parts)) != len(parts)
        or any(not re.fullmatch(r"[a-z][a-z0-9._:-]{0,159}", item) for item in parts)
        or "offline_access" not in parts
    ):
        raise XeroOAuthError("The requested Xero permissions are invalid. Start the connection again.")
    return " ".join(sorted(parts))


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def parse_callback(query_pairs):
    """Pass MultiDict.multi_items(), not a dict that silently loses duplicates."""
    allowed = {"state", "code", "error", "error_description", "error_uri", "scope", "session_state"}
    values, total = {}, 0
    try:
        for key, value in query_pairs:
            total += 1
            if (
                total > 12 or not isinstance(key, str) or key not in allowed or key in values
                or not isinstance(value, str) or len(value) > 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError()
            values[key] = value
    except (ValueError, TypeError):
        raise XeroOAuthError("The Xero callback is invalid. Start Connect to Xero again.") from None
    state, code, error = values.get("state"), values.get("code"), values.get("error")
    if (
        not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state)
        or (code is None) == (error is None)
        or (code is not None and (not code or len(code) > 4096 or code != code.strip()))
        or (error is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,199}", error))
    ):
        raise XeroOAuthError("The Xero callback is invalid. Start Connect to Xero again.")
    return {"state": state, "code": code, "error": error}


@dataclass(frozen=True)
class _Pending:
    verifier: str
    client_id: str
    redirect_uri: str
    scope: str
    captured: str
    persist: bool
    expires_at: float
    generation: int
    authorization_id: str


class XeroOAuth:
    def __init__(self, *, instance_id=None, client=None, clock=time.time):
        self.instance_id = instance_id or uuid4().hex
        if not isinstance(self.instance_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", self.instance_id):
            raise XeroOAuthError("The local Xero connection instance is invalid.")
        self.client, self.clock = client, clock
        self.lock = threading.RLock()
        self._pending, self._generation = {}, {}

    def invalidate(self, workspace_id):
        with self.lock:
            self._generation[workspace_id] = self._generation.get(workspace_id, 0) + 1
            self._pending = {
                state: pending for state, pending in self._pending.items()
                if json.loads(pending.captured)["id"] != workspace_id
            }

    def begin(self, client_id, redirect_uri, scope, captured_scope, persist=False):
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", client_id):
            raise XeroOAuthError("Enter the Client ID of your Xero PKCE application.")
        try:
            parsed = urlsplit(redirect_uri)
            if (
                not isinstance(redirect_uri, str) or len(redirect_uri) > 2048
                or parsed.scheme != "http" or parsed.hostname != "localhost"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not 1024 <= parsed.port <= 65535 or parsed.path != "/api/xero/callback"
                or redirect_uri != f"http://localhost:{parsed.port}/api/xero/callback"
            ):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise XeroOAuthError("Use this app's exact registered localhost Xero callback address.") from None
        if type(persist) is not bool:
            raise XeroOAuthError("Choose session-only or secure remembered storage explicitly.")
        captured, scope = _scope_snapshot(captured_scope), _scopes(scope)
        if captured_scope.get("instance_id") != self.instance_id:
            raise XeroOAuthError("The local app instance changed. Start Connect to Xero again.")
        now, state, verifier = self.clock(), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        with self.lock:
            self.invalidate(captured_scope["id"])
            self._pending = {key: value for key, value in self._pending.items() if value.expires_at > now}
            if len(self._pending) >= 64:
                raise XeroOAuthError("Too many Xero connections are pending. Finish a connection before starting another.")
            self._pending[state] = _Pending(
                verifier, client_id, redirect_uri, scope, captured, persist, now + AUTH_LIFETIME,
                self._generation[captured_scope["id"]], uuid4().hex,
            )
            pending = self._pending[state]
        query = urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": scope, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return {
            "authorization_url": AUTHORIZE_URL + "?" + query, "expires_at": pending.expires_at,
            "redirect_uri": redirect_uri, "authorization_id": pending.authorization_id,
        }

    def _current(self, current_scope):
        try:
            return _scope_snapshot(current_scope() if callable(current_scope) else current_scope)
        except Exception:
            raise XeroOAuthError("The workspace or Xero settings changed. Start Connect to Xero again.") from None

    def _exchange(self, pending, code):
        owned = self.client is None
        client = self.client or httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
        try:
            response = client.post(
                TOKEN_URL, data={
                    "grant_type": "authorization_code", "client_id": pending.client_id,
                    "code": code, "redirect_uri": pending.redirect_uri, "code_verifier": pending.verifier,
                }, headers={"Accept": "application/json"}, timeout=20, follow_redirects=False,
            )
            if response.status_code != 200 or len(response.content) > MAX_TOKEN_RESPONSE:
                raise XeroOAuthError("Xero could not complete the connection. Start Connect to Xero again.")
            value = json.loads(
                response.content, parse_float=Decimal,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                object_pairs_hook=_unique_pairs,
            )
            if not isinstance(value, dict):
                raise ValueError()
            access, refresh, expires = value.get("access_token"), value.get("refresh_token"), value.get("expires_in")
            if (
                value.get("error") or str(value.get("token_type", "")).lower() != "bearer"
                or type(expires) is not int or not 1 <= expires <= 86400
                or not isinstance(access, str) or not 1 <= len(access) <= 16384
                or not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in access + refresh)
            ):
                raise ValueError()
            granted = _scopes(value.get("scope", pending.scope))
            if not set(pending.scope.split()) <= set(granted.split()):
                raise ValueError()
            return {
                "client_id": pending.client_id, "access_token": access, "refresh_token": refresh,
                "expires_at": self.clock() + expires, "scope": granted,
            }
        except XeroOAuthError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, RecursionError):
            raise XeroOAuthError("Xero could not complete the connection. Start Connect to Xero again.") from None
        finally:
            if owned:
                client.close()

    def finish(self, *, state, code=None, error=None, current_scope):
        # Direct callers receive the same validation as the callback parser.
        if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state):
            raise XeroOAuthError("This Xero connection attempt is invalid or expired. Start Connect to Xero again.")
        with self.lock:
            pending = self._pending.pop(state, None)
        if pending is None:
            raise XeroOAuthError("This Xero connection attempt is invalid or expired. Start Connect to Xero again.")
        captured = json.loads(pending.captured)
        before = self._current(current_scope)
        with self.lock:
            if (
                self.clock() >= pending.expires_at
                or self._generation.get(captured["id"]) != pending.generation
                or before != pending.captured
            ):
                raise XeroOAuthError("The workspace or Xero settings changed, or the attempt expired. Start Connect to Xero again.")
        if error is not None:
            raise XeroOAuthError("Xero authorization was not completed. No connection was saved; start Connect to Xero again.")
        # No callback value is included in an exception, log or public result.
        parse_callback([("state", state), ("code", code)])
        bundle = self._exchange(pending, code)
        after = self._current(current_scope)
        with self.lock:
            if (
                self.clock() >= pending.expires_at
                or self._generation.get(captured["id"]) != pending.generation
                or after != pending.captured
            ):
                raise XeroOAuthError("The workspace or Xero settings changed during connection. Start Connect to Xero again.")
        return {
            "bundle": bundle, "captured_scope": captured, "persist": pending.persist,
            "authorization_id": pending.authorization_id,
        }
