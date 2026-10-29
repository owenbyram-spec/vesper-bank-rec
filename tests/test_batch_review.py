"""Batch review preserves distinct identities, all-or-nothing writes and scope."""

from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from vesper_bank_rec.app import create_app
from vesper_bank_rec.models import DomainError
from vesper_bank_rec.review import confirm_proposals


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url="http://127.0.0.1:8765") as client:
        client.get("/")
        client.headers.update({
            "Origin": "http://127.0.0.1:8765",
            "X-Vesper-CSRF": client.get("/api/bootstrap").json()["csrf_token"],
        })
        assert client.post("/api/demo", json={}).status_code == 200
        yield client


def request_body(state, **values):
    workspace, session = state["workspace"], state["session"]
    return {**values, "scope": {
        "workspace_id": workspace["id"], "session_id": session["id"],
        "input_version": session["input_version"], "revision": workspace["version"],
    }}


def test_selected_batch_confirms_four_distinct_pairs_with_audit_and_survives_restart(client, tmp_path):
    before = client.get("/api/state").json()
    ids = [p["id"] for p in before["session"]["drafts"]]
    response = client.post("/api/matches/confirm-selected", json=request_body(
        before, proposal_ids=ids, note="Operator reviewed all selected synthetic pairs against source rows.",
    ))
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 4
    after = client.get("/api/state").json()
    matches = after["session"]["matches"]
    assert len({m["bank_id"] for m in matches}) == len({m["ledger_id"] for m in matches}) == 4
    ledger = {r["id"]: r for r in after["session"]["ledger"]["rows"]}
    assert sum(ledger[m["ledger_id"]]["amount"] == "-75.00" for m in matches) == 2
    assert after["session"]["status"] != "completed"
    assert after["session"]["input_version"] == before["session"]["input_version"]
    assert after["workspace"]["version"] == before["workspace"]["version"] + 1
    assert len([e for e in after["session"]["history"] if e["action"] == "match_confirmed"]) == 4
    assert after["session"]["history"][-1]["action"] == "match_batch_reviewed"
    with TestClient(create_app(tmp_path, testing=True), base_url="http://127.0.0.1:8765") as resumed:
        resumed.get("/")
        assert resumed.get("/api/state").json()["session"]["matches"] == matches


@pytest.mark.parametrize("bad_kind", ["missing", "duplicate", "empty", "oversize", "non_string", "note"])
def test_invalid_batch_does_not_partially_change_saved_financial_records(client, bad_kind):
    before = client.get("/api/state").json()
    ids = [p["id"] for p in before["session"]["drafts"]]
    note = "Reviewed synthetic evidence"
    bad_ids = {
        "missing": [ids[0], "proposal_foreign"], "duplicate": [ids[0], ids[0]],
        "empty": [], "oversize": [f"proposal_{i}" for i in range(41)],
        "non_string": [ids[0], {}], "note": ids,
    }[bad_kind]
    if bad_kind == "note":
        note = ""
    response = client.post("/api/matches/confirm-selected", json=request_body(
        before, proposal_ids=bad_ids, note=note,
    ))
    assert response.status_code == 400
    assert client.get("/api/state").json()["workspace"] == before["workspace"]


@pytest.mark.parametrize("bad_kind", ["conflicting", "stale", "completed", "repeated_proposal_id"])
def test_domain_rejects_entire_conflicting_or_stale_selection_before_mutation(client, bad_kind):
    state = client.get("/api/state").json()
    workspace = state["workspace"]
    session = workspace["sessions"][0]
    selected = session["drafts"][:2]
    if bad_kind == "conflicting":
        selected[1]["bank_id"] = selected[0]["bank_id"]
        selected[1]["ledger_id"] = selected[0]["ledger_id"]
    elif bad_kind == "stale":
        selected[1]["input_version"] -= 1
    elif bad_kind == "completed":
        session["status"] = "completed"
    else:
        selected[1]["id"] = selected[0]["id"]
    before = deepcopy(workspace)
    with pytest.raises(DomainError):
        confirm_proposals(workspace, session, [p["id"] for p in selected], "Reviewed")
    assert workspace == before


def test_stale_tab_and_foreign_workspace_cannot_bulk_accept(client):
    before = client.get("/api/state").json()
    body = request_body(before, proposal_ids=[p["id"] for p in before["session"]["drafts"]], note="Reviewed")
    assert client.post("/api/workspaces/new", json={"name": "Separate client"}).status_code == 200
    assert client.post("/api/matches/confirm-selected", json=body).status_code == 400
    stale = client.post("/api/assistant", json={"message": "Accept them", "rehearsal": True},
                        headers={"X-Vesper-Scope": json.dumps(before["scope"])})
    assert stale.status_code == 409


def test_single_review_rejects_non_string_proposal_identity_without_writing(client):
    before = client.get("/api/state").json()
    response = client.post("/api/matches/confirm", json=request_body(
        before, proposal_id={"id": before["session"]["drafts"][0]["id"]}, note="Reviewed",
    ))
    assert response.status_code == 400
    assert client.get("/api/state").json()["workspace"] == before["workspace"]
