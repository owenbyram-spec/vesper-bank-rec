"""Scoped Xero authorization and read-only import routes for the local utility."""

from __future__ import annotations

from contextlib import contextmanager
import html
import threading
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .connectors.xero import XeroClient, XeroError
from .connectors.xero_oauth import XeroOAuth, XeroOAuthError, parse_callback
from .settings import SettingsError


XERO_SCOPES = (
    "offline_access", "accounting.settings.read", "accounting.banktransactions.read",
    "accounting.payments.read", "accounting.reports.banksummary.read",
)


class XeroConnections:
    def __init__(self, *, settings, runtime, workspace_getter, require_open):
        self.settings = settings
        self.runtime = runtime
        self.workspace_getter = workspace_getter
        self.require_open = require_open
        self.lock = threading.RLock()
        self.state_lock = threading.RLock()
        self.generations = {}
        self.authorizations = {}
        self.pending_states = {}
        self.active = 0
        self.oauth = XeroOAuth(instance_id=runtime["instance_id"])
        self.factory = lambda wid: XeroClient(settings, wid, lock=self.lock)

    @property
    def redirect_uri(self):
        return f"http://localhost:{self.runtime['port']}/api/xero/callback"

    def snapshot(self):
        current = self.workspace_getter(False) or {}
        with self.state_lock:
            generation = self.generations.get(current.get("id"), 0)
        return {**current, "instance_id": self.runtime["instance_id"], "xero_generation": generation}

    def invalidate(self, wid):
        with self.state_lock:
            self.generations[wid] = self.generations.get(wid, 0) + 1
            self.authorizations.pop(wid, None)
            self.pending_states.pop(wid, None)
        self.oauth.invalidate(wid)

    def status(self, scope):
        with self.state_lock:
            auth = dict(self.authorizations.get(scope["id"], {"status": "idle", "message": "Connect Xero for this workspace."}))
            if auth["status"] == "pending" and auth.get("expires_at", 0) <= time.time():
                auth = {"status": "expired", "message": "Xero authorization expired. Connect again."}
                self.authorizations[scope["id"]] = auth
        return {
            "xero": self.settings.public(scope["id"], include_xero=True)["xero"],
            "authorization": auth,
            "redirect_uri": self.redirect_uri,
            "scopes": list(XERO_SCOPES),
        }

    @contextmanager
    def operation(self):
        with self.state_lock:
            self.require_open()
            self.active += 1
        try:
            yield
        finally:
            with self.state_lock:
                self.active -= 1


def install_xero(app, *, settings, runtime, workspace, displayed_scope, same_scope,
                 require_open, operation_guard, sync, scope_lock=None):
    scope_lock = scope_lock or threading.RLock()
    connection = XeroConnections(settings=settings, runtime=runtime,
                                 workspace_getter=workspace, require_open=require_open)

    @app.exception_handler(XeroError)
    @app.exception_handler(XeroOAuthError)
    async def xero_error(_request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/settings/xero")
    def status(request: Request):
        scope = displayed_scope(request)
        return connection.status(scope)

    @app.post("/api/settings/xero/authorize")
    def authorize(body: dict, request: Request):
        scope = displayed_scope(request)
        if set(body) - {"client_id", "persist"} or type(body.get("persist", False)) is not bool:
            raise XeroOAuthError("Choose a Xero Client ID and storage preference.")
        with operation_guard(), connection.operation(), connection.lock, scope_lock:
            require_open()
            if not same_scope(scope):
                raise HTTPException(409, "The workspace changed. Start the Xero connection again.")
            if body.get("persist"):
                store = settings._persistent()
                if store is None or not store.persistent:
                    raise SettingsError("Secure storage is unavailable. Choose session-only storage explicitly.")
            connection.invalidate(scope["id"])
            captured = connection.snapshot()
            result = connection.oauth.begin(
                client_id=body.get("client_id"), redirect_uri=connection.redirect_uri,
                scope=" ".join(XERO_SCOPES), captured_scope=captured,
                persist=body.get("persist", False),
            )
            with connection.state_lock:
                connection.pending_states[scope["id"]] = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
                connection.authorizations[scope["id"]] = {
                    "status": "pending", "expires_at": result["expires_at"],
                    "message": "Complete authorization on Xero, then return here to check the connection.",
                }
            return {**connection.status(scope), **result}

    @app.post("/api/settings/xero/cancel")
    def cancel(request: Request):
        scope = displayed_scope(request)
        with operation_guard(), connection.lock, scope_lock:
            if not same_scope(scope):
                raise HTTPException(409, "The workspace changed. Cancel Xero setup in the intended workspace.")
            connection.invalidate(scope["id"])
            return {"ok": True, **connection.status(scope)}

    @app.get("/api/xero/callback", include_in_schema=False)
    def callback(request: Request):
        # Middleware captures the query then removes it from the ASGI scope so
        # Uvicorn cannot put authorization codes/state into its access log.
        message = "Xero authorization could not finish. Return to Bank Rec and connect again."
        status_code = 400
        target_workspace, callback_state = None, None
        try:
            values = parse_callback(getattr(request.state, "xero_callback_params", ()))
            callback_state = values["state"]
            with connection.state_lock:
                target_workspace = next((wid for wid, state in connection.pending_states.items() if state == callback_state), None)
            with operation_guard(), connection.operation():
                result = connection.oauth.finish(**values, current_scope=connection.snapshot)
                captured = result["captured_scope"]
                with connection.lock, scope_lock:
                    require_open()
                    if captured != connection.snapshot():
                        raise XeroOAuthError("The workspace changed. Return to Bank Rec and connect Xero again.")
                    bundle = {**result["bundle"], "connection_id": result["authorization_id"]}
                    settings.save(captured["id"], "xero", bundle, result["persist"])
                    with connection.state_lock:
                        connection.pending_states.pop(captured["id"], None)
                        connection.authorizations[captured["id"]] = {
                            "status": "connected",
                            "message": "Xero authorized. Return to Bank Rec and confirm the intended organisation.",
                        }
                message = "Xero authorized. Close this tab, return to Bank Rec and choose Check authorization to confirm the intended organisation."
                status_code = 200
        except (XeroOAuthError, XeroError, SettingsError, HTTPException):
            # Do not echo provider errors, query values, tokens or invalid state.
            with connection.state_lock:
                if target_workspace and connection.pending_states.get(target_workspace) == callback_state:
                    connection.pending_states.pop(target_workspace, None)
                    connection.authorizations[target_workspace] = {
                        "status": "failed", "message": message,
                    }
        body = (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Bank Rec · Xero connection</title><h1>Bank Rec · Xero connection</h1><p>"
            + html.escape(message) + "</p></html>"
        )
        return HTMLResponse(body, status_code=status_code)

    @app.delete("/api/settings/xero")
    def disconnect(request: Request):
        scope = displayed_scope(request)
        with operation_guard(), connection.operation(), connection.lock, scope_lock:
            require_open()
            if not same_scope(scope):
                raise HTTPException(409, "The workspace changed. Disconnect in the intended workspace.")
            connection.invalidate(scope["id"])
            settings.remove(scope["id"], "xero")
            return {"ok": True, **connection.status(scope)}

    @app.get("/api/xero/organisations")
    def organisations(request: Request):
        scope = displayed_scope(request)
        with operation_guard(), connection.operation(), connection.lock:
            if not same_scope(scope):
                raise HTTPException(409, "The workspace changed before the Xero read.")
            result = connection.factory(scope["id"]).organizations()
            if not same_scope(scope):
                raise HTTPException(409, "The workspace changed during the Xero read.")
            return {"organisations": result}

    @app.post("/api/xero/confirm")
    def confirm(body: dict, request: Request):
        scope = displayed_scope(request)
        if set(body) != {"tenant_id"}:
            raise XeroError("Choose the intended Xero organisation to confirm.")
        with operation_guard(), connection.operation(), connection.lock:
            with scope_lock:
                if not same_scope(scope):
                    raise HTTPException(409, "The workspace changed before company confirmation.")
                captured = connection.snapshot()
            client = connection.factory(scope["id"])
            # The provider read and final credential binding share the same lock.
            company = client.bind(body.get("tenant_id"), scope_check=lambda: captured == connection.snapshot(), scope_lock=scope_lock)
            if captured != connection.snapshot():
                raise HTTPException(409, "The workspace changed during confirmation. Review Xero in the intended workspace.")
            return {"company": company, **connection.status(scope)}

    @app.get("/api/xero/accounts")
    def accounts(request: Request):
        scope = displayed_scope(request)
        with operation_guard(), connection.operation(), connection.lock:
            if not same_scope(scope):
                raise HTTPException(409, "The workspace changed before the account read.")
            result = connection.factory(scope["id"]).accounts()
            if not same_scope(scope):
                raise HTTPException(409, "The workspace changed during the account read.")
            return {"accounts": result}

    @app.post("/api/xero/sync")
    def refresh(body: dict, request: Request):
        scope = displayed_scope(request)
        if set(body) - {"account_id", "start_date", "end_date"}:
            raise XeroError("Xero import accepts only the selected account and period.")
        with operation_guard(), connection.operation(), connection.lock:
            require_open()
            if not same_scope(scope):
                raise HTTPException(409, "The selected work changed before Xero import.")
            result = sync(connection.factory(scope["id"]), scope, body)
            return {"ok": True, "result": result}

    return connection
