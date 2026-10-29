"""Explicit match notes preserve operator evidence without inventing an identity."""

import json

import pytest
from fastapi.testclient import TestClient

from vesper_bank_rec.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url="http://127.0.0.1:8765") as client:
        client.get("/")
        token = client.get("/api/bootstrap").json()["csrf_token"]
        client.headers.update({"Origin": "http://127.0.0.1:8765", "X-Vesper-CSRF": token})
        assert client.post("/api/demo", json={}).status_code == 200
        yield client


def finance_post(client, path, body):
    state = client.get("/api/state").json()
    workspace, session = state["workspace"], state["session"]
    return client.post(
        path,
        json={
            **body,
            "scope": {
                "workspace_id": workspace["id"],
                "session_id": session["id"],
                "input_version": session["input_version"],
                "revision": workspace["version"],
            },
        },
        headers={"X-Vesper-Scope": json.dumps(state["scope"])},
    )


@pytest.mark.parametrize("note", [None, "", "   ", "x" * 2001])
def test_confirmation_requires_an_explicit_valid_review_note(client, note):
    before = client.get("/api/state").json()
    payload = {"proposal_id": before["session"]["drafts"][0]["id"]}
    if note is not None:
        payload["note"] = note
    response = finance_post(client, "/api/matches/confirm", payload)
    assert response.status_code == 400
    assert "Evidence reference / review note" in response.json()["error"]
    after = client.get("/api/state").json()
    assert after["workspace"] == before["workspace"]
    assert after["session"] == before["session"]


def test_future_attribution_and_prior_match_evidence_survive_reconfirmation_and_restart(client, tmp_path):
    before = client.get("/api/state").json()
    proposal = before["session"]["drafts"][0]
    historical_note = "Human reviewed proposed exact one-to-one match"
    response = finance_post(
        client, "/api/matches/confirm", {"proposal_id": proposal["id"], "note": historical_note}
    )
    assert response.status_code == 200
    old = client.get("/api/state").json()["session"]["matches"][0]
    assert old["note"] == historical_note
    assert finance_post(client, "/api/matches/unconfirm", {"match_id": old["id"]}).status_code == 200
    assert finance_post(client, "/api/matches/propose", {}).status_code == 200
    future_note = "Assistant demonstration at operator request; reviewed the synthetic exact signed pair."
    response = finance_post(
        client, "/api/matches/confirm", {"proposal_id": proposal["id"], "note": future_note}
    )
    assert response.status_code == 200

    with TestClient(create_app(tmp_path, testing=True), base_url="http://127.0.0.1:8765") as resumed:
        resumed.get("/")
        state = resumed.get("/api/state").json()
        match = state["session"]["matches"][0]
        assert match["note"] == future_note
        assert (match["bank_id"], match["ledger_id"]) == (old["bank_id"], old["ledger_id"])
        confirmations = [e for e in state["session"]["history"] if e["action"] == "match_confirmed"]
        assert confirmations[0]["detail"] == old
        assert confirmations[1]["detail"] == match
        assert state["session"]["status"] != "completed"
