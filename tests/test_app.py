import json
import pytest
from fastapi.testclient import TestClient
from vesper_bank_rec.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        assert c.get("/").status_code == 200
        token = c.get("/api/bootstrap").json()["csrf_token"]
        c.headers.update({"Origin": "http://127.0.0.1:8765", "X-Vesper-CSRF": token})
        yield c


def scope(c):
    state = c.get("/api/state").json()
    w = state["workspace"]
    s = state["session"]
    return {
        "workspace_id": w["id"],
        "session_id": s["id"] if s else None,
        "input_version": s["input_version"] if s else None,
        "revision": w["version"],
    }


def export_url(c, extension):
    from urllib.parse import urlencode

    return "/api/export/" + extension + "?" + urlencode(scope(c))


def post(c, url, body=None):
    payload = dict(body or {})
    if url.startswith(("/api/sessions/", "/api/imports/", "/api/matches/")) or url in (
        "/api/anchor",
        "/api/classifications",
    ):
        payload.setdefault("scope", scope(c))
    current_scope = c.get("/api/state").json().get("scope")
    headers = {"X-Vesper-Scope": json.dumps(current_scope)} if current_scope else {}
    result = c.post(url, json=payload, headers=headers)
    assert result.status_code == 200, result.text
    return result.json()


def loaded(c):
    post(c, "/api/demo")
    return c.get("/api/state").json()


def finish(c):
    state = c.get("/api/state").json()
    for proposal in state["session"]["drafts"]:
        post(c, "/api/matches/confirm", {"proposal_id": proposal["id"], "note": "Reviewed synthetic pair"})
    state = c.get("/api/state").json()
    check = next(r for r in state["session"]["ledger"]["rows"] if r["amount"] == "-300.00")
    post(
        c,
        "/api/classifications",
        {
            "row_id": check["id"],
            "kind": "outstanding",
            "evidence": "Synthetic issued-check schedule reviewed",
        },
    )
    assert c.get("/api/state").json()["summary"]["ready_to_complete"]
    post(c, "/api/sessions/complete", {"note": "Human acceptance of synthetic reconciliation"})


def test_complete_reopen_restart_and_exact_selected_exports(client, tmp_path):
    c = client
    state = loaded(c)
    session_id = state["session"]["id"]
    finish(c)
    result = c.get("/api/state").json()
    assert result["session"]["status"] == "completed"
    assert result["summary"]["adjusted_bank"] == result["summary"]["adjusted_books"] == "6050.00"
    for extension in ("csv", "xlsx", "html", "json"):
        response = c.get(export_url(c, extension))
        assert response.status_code == 200 and response.content
    assert session_id in c.get(export_url(c, "html")).text
    saved = c.get(export_url(c, "json")).json()
    restored = post(c, "/api/restore", saved)
    assert restored["workspace_id"] != result["workspace"]["id"]
    assert c.get("/api/state").json()["summary"]["adjusted_books"] == "6050.00"
    post(c, "/api/workspaces/select", {"workspace_id": result["workspace"]["id"]})
    post(c, "/api/sessions/reopen", {"note": "Synthetic review evidence check"})
    assert c.get("/api/state").json()["session"]["anchor"]["kind"] == "zero_outstanding"
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as resumed:
        resumed.get("/")
        assert resumed.get("/api/state").json()["summary"]["adjusted_books"] == "6050.00"


def test_same_month_accounts_export_cannot_cross_select(client):
    first = loaded(client)
    first_id = first["session"]["id"]
    post(
        client,
        "/api/sessions/new",
        {
            "account_id": "SECOND",
            "account_label": "Different synthetic account",
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
        },
    )
    assert client.get(export_url(client, "csv") + "&session_id=" + first_id).status_code == 400
    response = client.get(export_url(client, "csv"))
    assert "Different synthetic account" in response.text and first_id not in response.text
    assert "Client receipt" not in response.text
    post(client, "/api/sessions/select", {"session_id": first_id})
    assert "Client receipt" in client.get(export_url(client, "csv")).text


def test_workspace_scope_metadata_only_and_foreign_proposals_rejected(client):
    first = loaded(client)
    proposal = first["session"]["drafts"][0]
    post(client, "/api/workspaces/new", {"name": "Second synthetic workspace", "currency": "USD"})
    state = client.get("/api/state").json()
    assert not state["workspace"]["sessions"]
    assert all(set(w) == {"id", "name", "currency", "synthetic"} for w in state["workspaces"])
    assert first["session"]["id"] not in json.dumps(state)
    assert client.post("/api/sessions/select", json={"session_id": first["session"]["id"]}).status_code == 400
    assert client.post("/api/matches/confirm", json={"proposal_id": proposal["id"]}).status_code == 400
    assert (
        client.post("/api/workspaces/new", json={"name": "Bad precision", "currency": "KWD"}).status_code
        == 400
    )


def test_rehearsal_dispatches_distinct_real_evidence_without_credentials(client):
    loaded(client)
    response = post(
        client,
        "/api/assistant",
        {"message": "Explain the source controls and remaining work.", "rehearsal": True},
    )
    assert response["mode"] == "synthetic-rehearsal"
    assert response["usage"]["provider_calls"] == 0 and response["usage"]["tool_calls"] == 4
    assert len(response["evidence"]) == 4
    assert len({e["id"] for e in response["evidence"]}) == 4
    assert client.get("/api/state").json()["session"]["matches"] == []


def test_real_tool_draft_requires_human_confirmation_and_rejects_stale_model_state(client):
    state = loaded(client)
    pair = state["session"]["drafts"][0]

    class Fixture:
        def __init__(self):
            self.calls = 0

        def respond(self, messages, tools, remaining_seconds=20):
            self.calls += 1
            if self.calls == 1:
                assert not any("confirm" in t["name"] or "approve" in t["name"] for t in tools)
                return {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "synthetic-call",
                            "name": "draft_match",
                            "arguments": json.dumps(
                                {
                                    "bank_id": pair["bank_id"],
                                    "ledger_id": pair["ledger_id"],
                                    "reason": "Synthetic adapter evaluated exact amount",
                                }
                            ),
                        }
                    ]
                }
            evidence = json.loads(
                next(m["output"] for m in messages if m.get("type") == "function_call_output")
            )["evidence_id"]
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "summary": "The draft is ready for human review.",
                                        "evidence_ids": [evidence],
                                    }
                                ),
                            }
                        ],
                    }
                ]
            }

    post(
        client,
        "/api/settings/model",
        {
            "api_key": "synthetic-key-never-used",
            "model": "synthetic-fixture",
            "persist": False,
            "consent": True,
        },
    )
    client.app.state.common.adapter_factory = lambda bundle: Fixture()
    response = post(client, "/api/assistant", {"message": "Draft a match for my review.", "rehearsal": False})
    assert response["proposals"] and response["mode"] == "model"
    assert client.get("/api/state").json()["session"]["matches"] == []
    post(
        client,
        "/api/matches/confirm",
        {"proposal_id": response["proposals"][0]["id"], "note": "Human reviewed the exact proposed pair"},
    )
    assert len(client.get("/api/state").json()["session"]["matches"]) == 1


def test_bank_import_preview_is_bound_to_selected_workspace(client):
    loaded(client)
    payload = {
        "side": "bank",
        "csv": "date,description,amount,account,currency\n",
        "opening": "1.00",
        "closing": "1.00",
        "source": "Synthetic controlled empty statement",
    }
    preview = post(client, "/api/imports/preview", payload)
    post(client, "/api/workspaces/new", {"name": "Other", "currency": "USD"})
    post(
        client,
        "/api/sessions/new",
        {"account_id": "SYNTH-CHECKING", "period_start": "2026-09-01", "period_end": "2026-09-30"},
    )
    assert client.post("/api/imports/commit", json={"preview_id": preview["id"]}).status_code == 400
    assert client.get("/api/state").json()["session"]["bank"] is None


def test_qbo_callback_refuses_scope_switch_during_network_and_preserves_valid_source(client):
    state = loaded(client)
    old_hash = state["session"]["ledger"]["source_hash"]
    first_id = state["workspace"]["id"]
    store = client.app.state.store

    class SyntheticQBO:
        def _require_bound(self):
            pass

        def bank_ledger(self, **kwargs):
            store.create("Switched during read", "USD", True)
            return {
                "rows": [],
                "opening_balance": "5000.00",
                "closing_balance": "5000.00",
                "currency": "USD",
                "source": {"complete": True, "synthetic": True},
            }

    client.app.state.common.qbo_factory = lambda workspace_id: SyntheticQBO()
    response = client.post(
        "/api/qbo/sync",
        json={"account_id": "SYNTH-CHECKING", "start_date": "2026-09-01", "end_date": "2026-09-30"},
        headers={"X-Vesper-Scope": json.dumps(client.get("/api/state").json()["scope"])},
    )
    assert response.status_code == 400
    assert store.active()["name"] == "Switched during read"
    store.select(first_id)
    assert store.active()["sessions"][0]["ledger"]["source_hash"] == old_hash


def test_qbo_normalized_records_use_identical_controls_and_only_current_session(client):
    loaded(client)

    class SyntheticQBO:
        def _require_bound(self):
            pass

        def bank_ledger(self, **kwargs):
            assert kwargs == {
                "account_id": "SYNTH-CHECKING",
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
            }
            return {
                "rows": [
                    {
                        "id": "synthetic-vendor-row",
                        "date": "2026-09-04",
                        "description": "Synthetic QBO fixture receipt",
                        "amount": "25.00",
                        "source_ids": ["synthetic-source"],
                        "source_ordinal": 1,
                    }
                ],
                "opening_balance": "5000.00",
                "closing_balance": "5025.00",
                "currency": "USD",
                "source": {"complete": True, "synthetic": True, "digest": "synthetic-digest"},
            }

    client.app.state.common.qbo_factory = lambda workspace_id: SyntheticQBO()
    post(
        client,
        "/api/qbo/sync",
        {"account_id": "SYNTH-CHECKING", "start_date": "2026-09-01", "end_date": "2026-09-30"},
    )
    source = client.get("/api/state").json()["session"]["ledger"]
    assert source["activity"] == "25.00" and source["closing"] == "5025.00" and source["ready"]
    assert source["rows"][0]["provider_source_ids"] == ["synthetic-source"]
    assert (
        client.post(
            "/api/qbo/sync",
            json={"account_id": "FOREIGN"},
            headers={"X-Vesper-Scope": json.dumps(client.get("/api/state").json()["scope"])},
        ).status_code
        == 400
    )
    assert client.get("/api/state").json()["session"]["ledger"]["source_hash"] == source["source_hash"]


def prepare_ready_without_completing(c):
    state = c.get("/api/state").json()
    for proposal in state["session"]["drafts"]:
        post(c, "/api/matches/confirm", {"proposal_id": proposal["id"], "note": "Reviewed synthetic pair"})
    state = c.get("/api/state").json()
    check = next(r for r in state["session"]["ledger"]["rows"] if r["amount"] == "-300.00")
    post(
        c,
        "/api/classifications",
        {"row_id": check["id"], "kind": "outstanding", "evidence": "Synthetic check register"},
    )
    assert c.get("/api/state").json()["summary"]["ready_to_complete"]


def test_stale_tab_cannot_complete_anchor_or_create_in_other_workspace(client):
    loaded(client)
    prepare_ready_without_completing(client)
    tab_a_scope = scope(client)
    tab_a_export = export_url(client, "csv")
    loaded(client)
    prepare_ready_without_completing(client)
    tab_b = client.get("/api/state").json()
    assert tab_b["workspace"]["id"] != tab_a_scope["workspace_id"]
    before = json.dumps(tab_b["workspace"], sort_keys=True)
    # Tab A still displays its original client while tab B changed server context.
    attempts = [
        ("/api/sessions/complete", {"note": "Stale tab acceptance"}),
        (
            "/api/anchor",
            {
                "kind": "zero_outstanding",
                "as_of": "2026-08-31",
                "evidence": "Stale opening evidence",
                "outstanding": [],
            },
        ),
        ("/api/matches/propose", {}),
        (
            "/api/sessions/new",
            {"account_id": "ACCIDENTAL", "period_start": "2026-10-01", "period_end": "2026-10-31"},
        ),
    ]
    for path, body in attempts:
        response = client.post(path, json={**body, "scope": tab_a_scope})
        assert response.status_code == 400, response.text
        assert "another tab" in response.text
    assert json.dumps(client.get("/api/state").json()["workspace"], sort_keys=True) == before
    assert client.get(tab_a_export).status_code == 400
    assert client.get("/api/export/csv").status_code == 422


def test_finance_requests_require_displayed_scope_and_reject_stale_revision(client):
    loaded(client)
    assert client.post("/api/matches/propose", json={}).status_code == 400
    previous = scope(client)
    post(client, "/api/matches/propose")
    response = client.post("/api/matches/propose", json={"scope": previous})
    assert response.status_code == 400 and "revision changed" in response.text
    # Missing-null versus actual-null scope is checked for a new empty workspace.
    post(client, "/api/workspaces/new", {"name": "Empty synthetic scope test", "currency": "USD"})
    empty_scope = scope(client)
    assert empty_scope["session_id"] is None and empty_scope["input_version"] is None
    post(
        client,
        "/api/sessions/new",
        {"account_id": "EMPTY", "period_start": "2026-09-01", "period_end": "2026-09-30"},
    )
    response = client.post(
        "/api/sessions/new",
        json={
            "scope": empty_scope,
            "account_id": "OTHER",
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
        },
    )
    assert response.status_code == 400


def test_shared_settings_reads_and_assistant_reject_stale_displayed_scope(client):
    tab_a = loaded(client)["scope"]
    headers_a = {"X-Vesper-Scope": json.dumps(tab_a)}
    assert client.get("/api/settings", headers=headers_a).status_code == 200
    loaded(client)
    for path in ("/api/settings", "/api/qbo/accounts"):
        result = client.get(path, headers=headers_a)
        assert result.status_code == 409, result.text
    result = client.post(
        "/api/assistant", json={"message": "Explain the current work.", "rehearsal": True}, headers=headers_a
    )
    assert result.status_code == 409, result.text


def test_workspace_delete_stops_polling_and_removes_only_its_connections(client):
    state = loaded(client)
    wid = state["workspace"]["id"]
    common = client.app.state.common
    common.settings.set_model(wid, {"api_key": "synthetic-key", "model": "synthetic", "consent": True})
    common.settings.set_model(
        "different-synthetic-workspace", {"api_key": "synthetic-other", "model": "synthetic", "consent": True}
    )
    common.polling.configure(enabled=True, interval_seconds=60, params={}, workspace_id=wid)
    response = client.request(
        "DELETE",
        "/api/workspaces/current",
        json={"confirm_name": state["workspace"]["name"], "scope": scope(client)},
        headers={"X-Vesper-Scope": json.dumps(state["scope"])},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/state").json()["workspace"] is None
    assert not common.polling.status()["enabled"]
    assert common.settings.get(wid, "model") is None
    assert common.settings.get("different-synthetic-workspace", "model")["api_key"] == "synthetic-other"
