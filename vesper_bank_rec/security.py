"""Per-launch loopback session and cross-site request protection."""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, port: int, testing: bool = False, shutting_down=None, xero_callback_path=None):
        super().__init__(app)
        self.port = port
        self.testing = testing
        self.shutting_down = shutting_down
        self.xero_callback_path = xero_callback_path
        self.session = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        # Each app/port has a separate browser session even on the same host.
        self.cookie_name = f"vesper_session_{port}"

    def _valid_host(self, value: str) -> bool:
        allowed = {f"127.0.0.1:{self.port}"}
        if self.port == 80:
            allowed.add("127.0.0.1")
        if self.testing:
            allowed.add("testserver")
        return value.lower() in allowed

    def _valid_origin(self, value: str) -> bool:
        try:
            parsed = urlsplit(value)
            return (
                parsed.scheme == "http"
                and not parsed.username
                and not parsed.password
                and parsed.path == ""
                and not parsed.query
                and not parsed.fragment
                and self._valid_host(parsed.netloc)
            )
        except ValueError:
            return False

    async def dispatch(self, request: Request, call_next):
        def denied(message):
            return self._headers(JSONResponse({"detail": message}, status_code=403))

        callback_path = request.url.path == self.xero_callback_path
        if callback_path:
            raw_query = request.scope.get("query_string", b"")
            # Capture only for the callback handler and redact the shared ASGI
            # scope before Uvicorn formats its access log (including failures).
            request.state.xero_callback_params = (
                list(request.query_params.multi_items()) if len(raw_query) <= 16000 else []
            )
            request.scope["query_string"] = b""
        oauth_callback = (
            callback_path and request.method == "GET"
            and request.headers.get("host", "").lower() == f"localhost:{self.port}"
        )
        if callback_path and not oauth_callback:
            return denied("Use the registered localhost Xero callback address.")
        if not oauth_callback and not self._valid_host(request.headers.get("host", "")):
            return denied("Open the app at its displayed 127.0.0.1 address.")
        origin = request.headers.get("origin")
        if not oauth_callback and origin is not None and not self._valid_origin(origin):
            return denied("Cross-site requests are not allowed.")
        if not oauth_callback and request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            return denied("Use the app's local browser tab.")
        is_api = request.url.path == "/api" or request.url.path.startswith("/api/")
        if is_api and not oauth_callback:
            if not secrets.compare_digest(
                request.cookies.get(self.cookie_name, ""), self.session
            ):
                return denied("Open the local app before using its API.")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if not origin or not secrets.compare_digest(
                    request.headers.get("x-vesper-csrf", ""), self.csrf_token
                ):
                    return denied("Refresh the app and retry this action.")
            request.state.vesper_csrf = self.csrf_token
            if (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and self.shutting_down is not None
                and self.shutting_down()
            ):
                return self._headers(JSONResponse(
                    {"detail": "Bank Rec is closing. Open the app again to continue; your saved work is preserved."},
                    status_code=409,
                ))
        if oauth_callback and self.shutting_down is not None and self.shutting_down():
            return self._headers(JSONResponse({"detail": "Bank Rec is closing. Connect Xero after opening it again."}, status_code=409))
        response = await call_next(request)
        if request.method == "GET" and request.url.path == "/":
            response.set_cookie(
                self.cookie_name,
                self.session,
                httponly=True,
                samesite="strict",
                path="/",
            )
        return self._headers(response)

    @staticmethod
    def _headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        # The packaged UI needs no external script or frame origin.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response
