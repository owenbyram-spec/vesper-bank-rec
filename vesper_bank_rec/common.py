"""Common local-only API routes shared by each independently packaged app."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import asyncio
from pathlib import Path
import threading
import sys
import uuid

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .assistant import AssistantError, ResponsesAdapter, run_assistant
from .assistant.attempts import AssistantAttempts
from .connectors import QBOClient, QBOError
from .jobs import PollingJob, RefreshBusy
from .security import LocalSecurityMiddleware
from .settings import MemorySecretStore, SettingsError, SettingsService


@dataclass
class CommonServices:
    settings: SettingsService
    polling: PollingJob
    qbo_factory: object
    adapter_factory: object
    xero: object = None
    attempts: object = None

    @property
    def jobs(self):
        return self.polling


def install_common(
    app: FastAPI,
    *,
    data_dir: Path,
    port: int,
    workspace_getter,
    tools_factory,
    qbo_sync,
    xero_sync=None,
    scope_lock=None,
    testing: bool = False,
) -> CommonServices:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = SettingsService(data_dir, MemorySecretStore() if testing else None, testing=testing)
    qbo_lock = threading.RLock()
    state_lock = threading.RLock()
    active_runs = {}
    model_tests = {}
    active_model_tests = set()
    active_connections = set()
    runtime = {
        "application": "vesper-bank-rec",
        "packaged": bool(getattr(sys, "frozen", False)),
        "instance_id": uuid.uuid4().hex,
        "port": port,
        "source_dir": str(Path(__file__).resolve().parent.parent),
        "data_dir": str(data_dir.resolve()),
        "testing": testing,
    }
    app.state.runtime = runtime
    app.state.shutting_down = False
    run_file = data_dir / "assistant-runs.jsonl"

    def connection_status(scope):
        public = settings.public(scope["id"])
        with state_lock:
            receipt = model_tests.get(scope["id"])
        return {**public, "scope": scope, "runtime": runtime, "model_test": receipt}

    def require_open():
        if app.state.shutting_down:
            raise HTTPException(409, "Bank Rec is closing. Open the app again to continue.")

    @contextmanager
    def connection_operation():
        marker = uuid.uuid4().hex
        with state_lock:
            require_open()
            active_connections.add(marker)
        try:
            yield
        finally:
            with state_lock:
                active_connections.discard(marker)

    def workspace(required=True):
        scope = workspace_getter()
        if not isinstance(scope, dict) or not isinstance(scope.get("id"), str) or not scope["id"]:
            if required:
                raise HTTPException(409, "Create or select a workspace first.")
            return None
        return dict(scope)

    def same_scope(saved):
        now = workspace(False)
        fields = (
            "id",
            "version",
            "session_id",
            "input_version",
            "version_id",
            "input_hash",
            "result_hash",
            "account_id",
            "period_start",
            "period_end",
        )
        return bool(
            now and all(now.get(key) == saved.get(key) for key in fields if key in now or key in saved)
        )

    def displayed_scope(request: Request):
        current = workspace()
        try:
            raw = request.headers.get("X-Vesper-Scope", "")
            if len(raw) > 4000:
                raise ValueError()
            displayed = json.loads(raw)
            if not isinstance(displayed, dict) or displayed != current:
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(
                409, "The displayed workspace or version changed. Refresh this page before continuing."
            ) from None
        # Use the snapshot that passed validation; never select scope from a request.
        return current

    def execute_sync(params, workspace_id, captured=None):
        require_open()
        scope = captured or workspace(False)
        if captured and not same_scope(captured):
            raise HTTPException(409, "The displayed workspace or version changed before refresh started.")
        if not scope or scope["id"] != workspace_id:
            polling.stop()
            raise QBOError(
                "The active workspace changed. Polling stopped; select and reconnect the intended workspace."
            )
        client = services.qbo_factory(scope["id"])
        client._require_bound()
        result = qbo_sync(client, scope, params)
        # The callback must validate its captured scope/version immediately before commit.
        return result

    polling = PollingJob(execute_sync)
    services = CommonServices(
        settings,
        polling,
        lambda workspace_id: QBOClient(settings, workspace_id, lock=qbo_lock),
        lambda bundle: ResponsesAdapter(api_key=bundle["api_key"], model=bundle["model"]),
    )
    services.attempts = AssistantAttempts(data_dir)
    app.state.common = services
    app.add_middleware(
        LocalSecurityMiddleware, port=port, testing=testing,
        shutting_down=lambda: app.state.shutting_down,
        xero_callback_path="/api/xero/callback",
    )

    from .xero_routes import install_xero

    def execute_xero(client, captured, params):
        callback = xero_sync or qbo_sync
        try:
            return polling.run(params, captured["id"], callback=lambda p, _wid: callback(client, captured, p))
        except RefreshBusy:
            raise HTTPException(409, "An accounting refresh is already running. Wait for it to finish before importing Xero.") from None

    services.xero = install_xero(
        app, settings=settings, runtime=runtime, workspace=workspace,
        displayed_scope=displayed_scope, same_scope=same_scope,
        require_open=require_open, operation_guard=connection_operation, sync=execute_xero,
        scope_lock=scope_lock,
    )

    @app.exception_handler(RequestValidationError)
    async def safe_validation(_request, _exc):
        # FastAPI's normal validation envelope can echo secrets supplied in a bad body.
        return JSONResponse(
            {"detail": "The request format is invalid. Review the form and try again."},
            status_code=422,
        )

    @app.exception_handler(SettingsError)
    @app.exception_handler(QBOError)
    @app.exception_handler(AssistantError)
    async def safe_domain_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        return {
            "csrf_token": request.state.vesper_csrf,
            "local_only": True,
            "workspace": workspace(False),
            "runtime": runtime,
            "desktop_mode": bool(getattr(app.state, "desktop", False)),
        }

    @app.post("/api/app/quit")
    def quit_desktop(request: Request, background_tasks: BackgroundTasks):
        shutdown = getattr(app.state, "desktop_shutdown", None)
        if not getattr(app.state, "desktop", False) or not callable(shutdown):
            raise HTTPException(409, "Close this source launcher's window to stop the app.")
        with state_lock:
            if active_runs or active_model_tests or active_connections:
                raise HTTPException(409, "Wait for the assistant request or connection check to finish, then quit the app.")
            if not polling.run_lock.acquire(blocking=False):
                raise HTTPException(409, "Wait for the accounting refresh to finish, then quit the app.")
            try:
                app.state.shutting_down = True
                polling.stop()
            finally:
                polling.run_lock.release()
        # Return the acknowledgement before stopping the local HTTP server.
        background_tasks.add_task(shutdown)
        return {"ok": True, "message": "Bank Rec is closed. Your saved financial work is preserved."}

    @app.get("/api/settings")
    def get_settings(request: Request):
        scope = workspace(False)
        if scope:
            scope = displayed_scope(request)
        if not scope:
            polling.stop()
            return {
                "workspace_required": True,
                "model": {"configured": False},
                "qbo": {"configured": False},
                "polling": polling.status(),
                "runtime": runtime,
                "scope": None,
            }
        status = polling.status()
        if status["enabled"] and status["workspace_id"] != scope["id"]:
            polling.stop()
        return {
            **connection_status(scope),
            "workspace_required": False,
            "polling": polling.status(),
        }

    @app.post("/api/settings/model")
    def set_model(body: dict, request: Request):
        scope = displayed_scope(request)
        with state_lock:
            require_open()
            storage = settings.set_model(scope["id"], body)
            model_tests.pop(scope["id"], None)
        return {"ok": True, "saved_storage": storage, **connection_status(scope)}

    @app.delete("/api/settings/model")
    def remove_model(request: Request):
        scope = displayed_scope(request)
        with state_lock:
            require_open()
            if scope["id"] in active_runs:
                active_runs[scope["id"]].set()
            settings.remove(scope["id"], "model")
            model_tests.pop(scope["id"], None)
        return {"ok": True, **connection_status(scope)}

    @contextmanager
    def model_test_guard(workspace_id):
        with state_lock:
            require_open()
            if workspace_id in active_model_tests:
                raise HTTPException(409, "A connection test is already running for this workspace. Wait for it to finish.")
            active_model_tests.add(workspace_id)
            model_tests.pop(workspace_id, None)
        try:
            yield
        finally:
            with state_lock:
                active_model_tests.discard(workspace_id)

    def check_saved_model(scope):
        bundle = settings.get(scope["id"], "model")
        if not bundle or not bundle.get("consent"):
            raise SettingsError("Save an AI connection in this workspace first.")
        # Explicit user action; minimal synthetic prompt only, and disclosed as billable.
        response = services.adapter_factory(bundle).respond(
            [{"role": "user", "content": "Reply with the word connected."}], [], 20
        )
        texts = [
            block.get("text", "")
            for item in response.get("output", [])
            if isinstance(item, dict) and item.get("type") == "message"
            and isinstance(item.get("content"), list)
            for block in item.get("content", [])
            if isinstance(block, dict) and block.get("type") == "output_text"
        ]
        if (
            response.get("status") != "completed"
            or response.get("error")
            or "".join(texts).strip().lower() != "connected"
        ):
            raise AssistantError("The model did not complete the connection check. No successful test was recorded.")
        with state_lock:
            if not same_scope(scope) or settings.get(scope["id"], "model") != bundle:
                raise HTTPException(409, "The workspace or saved model settings changed during the test. Test again.")
            model_tests[scope["id"]] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "model": bundle["model"],
                "instance_id": runtime["instance_id"],
                "workspace_id": scope["id"],
            }
        return {
            "ok": True,
            "message": "The saved model connection completed the check for this workspace and app instance. This test can incur API usage.",
            **connection_status(scope),
        }

    @app.post("/api/settings/model/test")
    def test_model(request: Request):
        scope = displayed_scope(request)
        with model_test_guard(scope["id"]):
            return check_saved_model(scope)

    @app.post("/api/settings/qbo")
    def set_qbo(body: dict, request: Request):
        scope = displayed_scope(request)
        if polling.status()["running"]:
            raise HTTPException(409, "Wait for the current refresh before replacing the connection.")
        polling.stop()
        with qbo_lock:
            storage = settings.set_qbo(scope["id"], body)
        return {
            "ok": True,
            "storage": storage,
            "message": "Connection saved. Test it, then confirm the displayed company.",
            **settings.public(scope["id"]),
        }

    @app.post("/api/settings/qbo/test")
    def test_qbo(request: Request, body: dict | None = None):
        scope = displayed_scope(request)
        client = services.qbo_factory(scope["id"])
        company = client.company()
        if not same_scope(scope):
            raise HTTPException(409, "The workspace changed. Test again in the selected workspace.")
        confirm = (body or {}).get("confirm_company_id")
        if confirm is not None:
            company = client.bind(confirm)
            return {
                "ok": True,
                "company": company,
                "requires_confirmation": False,
                "message": "This company is bound to the current local workspace.",
            }
        return {
            "ok": True,
            "company": company,
            "requires_confirmation": True,
            "message": "Confirm that this is the intended company before reading its financial data.",
        }

    @app.delete("/api/settings/qbo")
    def disconnect_qbo(request: Request):
        scope = displayed_scope(request)
        polling.stop()
        # Drain an in-flight refresh so it cannot restore rotated credentials after disconnect.
        with polling.run_lock:
            with qbo_lock:
                settings.remove(scope["id"], "qbo")
        return {
            "ok": True,
            "message": "Local QBO credentials removed and polling stopped. Existing imported data is retained. Revoke the grant in Intuit to revoke provider access.",
        }

    @app.get("/api/qbo/accounts")
    def accounts(request: Request):
        scope = displayed_scope(request)
        result = services.qbo_factory(scope["id"]).accounts()
        if not same_scope(scope):
            raise HTTPException(409, "The workspace changed during the read.")
        return {
            "accounts": [
                {
                    "id": str(a["Id"]),
                    "name": str(a.get("FullyQualifiedName", a.get("Name", "Unnamed")))[:250],
                    "type": a.get("AccountType"),
                    "currency": a.get("CurrencyRef", {}).get("value"),
                    "parent_id": a.get("ParentRef", {}).get("value"),
                }
                for a in result
                if a.get("AccountType") == "Bank"
            ]
        }

    @app.post("/api/qbo/sync")
    def sync(body: dict, request: Request):
        scope = displayed_scope(request)
        if len(json.dumps(body)) > 10000:
            raise HTTPException(400, "Refresh parameters exceed the allowed size.")
        # params is the documented envelope; accept a direct params dict for straightforward clients.
        params = body.get("params", body)
        if not isinstance(params, dict):
            raise HTTPException(400, "Refresh parameters must be an object.")
        try:
            return {
                "ok": True,
                "result": polling.run(
                    params, scope["id"], callback=lambda p, wid: execute_sync(p, wid, captured=scope)
                ),
                "polling": polling.status(),
            }
        except (QBOError, SettingsError, HTTPException):
            raise
        except Exception:
            raise HTTPException(
                400,
                "The refresh did not pass source or workflow controls. Previous valid data is unchanged.",
            ) from None

    @app.post("/api/qbo/polling")
    def configure_polling(body: dict, request: Request):
        scope = displayed_scope(request)
        enabled = body.get("enabled")
        if type(enabled) is not bool or not isinstance(body.get("params", {}), dict):
            raise HTTPException(400, "Choose enabled true or false and valid refresh parameters.")
        if enabled:
            services.qbo_factory(scope["id"])._require_bound()
        try:
            status = polling.configure(
                enabled=enabled,
                interval_seconds=body.get("interval_seconds", 300),
                params=body.get("params", {}),
                workspace_id=scope["id"],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True, "polling": status}

    def record_run(run_id, scope, status, result=None):
        entry = {
            "id": run_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "workspace_hash": sha256(scope["id"].encode()).hexdigest(),
            "status": status,
            "mode": result.get("mode") if result else None,
            "usage": result.get("usage") if result else None,
            "evidence_ids": [e["id"] for e in result.get("evidence", [])] if result else [],
            "proposal_ids": [
                str(p.get("id", p.get("proposal_id", "draft")))[:180] for p in result.get("proposals", [])
            ]
            if result
            else [],
        }
        with state_lock:
            with run_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

    @app.post("/api/assistant")
    def assistant(body: dict, request: Request):
        scope = displayed_scope(request)
        rehearsal = body.get("rehearsal", False)
        if type(rehearsal) is not bool:
            raise AssistantError("Choose an explicit assistant mode.")
        if rehearsal and scope.get("synthetic") is not True:
            raise AssistantError("Synthetic rehearsal is available only in a labelled synthetic workspace.")
        message = body.get("message", "")
        if not isinstance(message, str) or not message.strip() or len(message) > 8000:
            raise AssistantError("Enter a question within the message length limit.")
        cancelled = threading.Event()
        with state_lock:
            require_open()
            if scope["id"] in active_runs:
                raise HTTPException(409, "An assistant run is already active. Cancel it or wait.")
            active_runs[scope["id"]] = cancelled
        request_id = body.get("request_id", uuid.uuid4().hex)
        claimed = False
        run_id = "run-" + uuid.uuid4().hex
        try:
            replay = services.attempts.claim(request_id, scope, message, rehearsal)
            if replay is not None:
                if not same_scope(scope):
                    raise AssistantError("The workspace or evidence changed. Ask again in the selected period.")
                return {**replay, "replayed": True}
            claimed = True
            record_run(run_id, scope, "started")
            bundle = None if rehearsal else settings.get(scope["id"], "model")
            result = run_assistant(
                message=message,
                tools=tools_factory(scope),
                rehearsal=rehearsal,
                settings=bundle,
                adapter=services.adapter_factory(bundle) if bundle else None,
                cancelled=cancelled,
                scope_check=lambda: same_scope(scope),
            )
            record_run(run_id, scope, "succeeded", result)
            response = {**result, "run_id": run_id}
            services.attempts.finish(request_id, "succeeded", response)
            return response
        except (AssistantError, SettingsError, HTTPException):
            if claimed:
                status = "cancelled" if cancelled.is_set() else "failed"
                services.attempts.finish(request_id, status)
                record_run(run_id, scope, status)
            raise
        except Exception:
            if claimed:
                services.attempts.finish(request_id, "failed")
                record_run(run_id, scope, "failed")
            raise HTTPException(
                400,
                "The assistant could not complete this run. Your workflow is saved.",
            ) from None
        finally:
            with state_lock:
                active_runs.pop(scope["id"], None)

    @app.post("/api/assistant/cancel")
    def cancel_assistant(request: Request):
        scope = displayed_scope(request)
        with state_lock:
            event = active_runs.get(scope["id"])
            if event:
                event.set()
        return {
            "ok": True,
            "message": "Cancellation requested. An in-flight provider request may finish before the run stops.",
        }

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def common_lifespan(application):
        async with previous_lifespan(application) as state:
            app.state.shutting_down = False
            try:
                yield state
            finally:
                app.state.shutting_down = True
                polling.stop()
                with state_lock:
                    for event in active_runs.values():
                        event.set()
                def drain_refresh():
                    with polling.run_lock:
                        pass
                    with services.xero.lock:
                        pass
                # The desktop retains its data lock until this lifespan exits.
                # Background accounting reads may finish, but cannot outlive ownership.
                await asyncio.to_thread(drain_refresh)

    app.router.lifespan_context = common_lifespan
    return services
