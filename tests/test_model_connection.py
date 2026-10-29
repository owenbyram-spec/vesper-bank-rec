"""Connection continuity and truthful checks using synthetic adapters, never a live account."""

from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import vesper_bank_rec
from vesper_bank_rec.app import create_app
from vesper_bank_rec.assistant import ResponsesAdapter


SECRET = "synthetic-model-key-never-sent-to-a-provider"
REPLACEMENT = "synthetic-replacement-key-never-sent-to-a-provider"


@pytest.fixture(autouse=True)
def forbid_live_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Connection regressions must use an injected adapter, never a live provider")

    monkeypatch.setattr(ResponsesAdapter, "respond", forbidden)


def open_client(app):
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    client.get("/")
    token = client.get("/api/bootstrap").json()["csrf_token"]
    client.headers.update({"Origin": "http://127.0.0.1:8765", "X-Vesper-CSRF": token})
    return client


def refresh_scope(client):
    state = client.get("/api/state").json()
    if state["scope"]:
        client.headers["X-Vesper-Scope"] = json.dumps(state["scope"])
    else:
        client.headers.pop("X-Vesper-Scope", None)
    return state


@pytest.fixture
def client(tmp_path):
    with open_client(create_app(tmp_path, testing=True)) as client:
        assert client.post("/api/demo", json={}).status_code == 200
        refresh_scope(client)
        yield client


def save_model(client, key=SECRET):
    refresh_scope(client)
    response = client.post(
        "/api/settings/model",
        json={"api_key": key, "model": "synthetic-model", "consent": True, "persist": False},
    )
    assert response.status_code == 200, response.text
    assert key not in response.text
    return response.json()


def completed_text(text="connected"):
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    }


class FixedAdapter:
    def __init__(self, response=None, on_respond=None):
        self.response = completed_text() if response is None else response
        self.on_respond = on_respond

    def respond(self, messages, tools, remaining_seconds=20):
        assert tools == []  # Connection tests transmit no workflow tools or financial evidence.
        assert messages == [{"role": "user", "content": "Reply with the word connected."}]
        if self.on_respond:
            self.on_respond()
        return deepcopy(self.response)


class ScopedExplanationAdapter:
    """Complete a real-mode handler path by asking its actual deterministic summary tool."""

    def __init__(self, expected_summary):
        self.expected_summary = expected_summary
        self.connection_calls = 0
        self.assistant_calls = 0

    def respond(self, messages, tools, remaining_seconds=20):
        if not tools:
            self.connection_calls += 1
            return FixedAdapter().respond(messages, tools, remaining_seconds)
        self.assistant_calls += 1
        assert "reconciliation_summary" in {tool["name"] for tool in tools}
        assert not any("confirm" in tool["name"] or "approve" in tool["name"] for tool in tools)
        outputs = [item for item in messages if item.get("type") == "function_call_output"]
        if not outputs:
            return {
                "status": "completed",
                "output": [{
                    "type": "function_call",
                    "call_id": "synthetic-summary-call",
                    "name": "reconciliation_summary",
                    "arguments": "{}",
                }],
            }
        assert len(outputs) == 1
        result = json.loads(outputs[0]["output"])
        assert result == self.expected_summary
        return completed_text(json.dumps({
            "summary": "Review the remaining check against the selected evidence.",
            "evidence_ids": [result["evidence_id"]],
        }))


def test_save_test_and_real_mode_scoped_explanation_preserve_finance_and_redact_secrets(client, tmp_path):
    before = refresh_scope(client)
    adapter = ScopedExplanationAdapter(before["summary"])
    client.app.state.common.adapter_factory = lambda bundle: adapter
    saved = save_model(client)
    assert saved["saved_storage"] == saved["model"]["storage"] == "session-only"
    assert saved["model"]["configured"] and saved["model"]["consent"]
    assert saved["scope"] == before["scope"] and saved["model_test"] is None
    tested = client.post("/api/settings/model/test", json={})
    assert tested.status_code == 200, tested.text
    receipt = tested.json()["model_test"]
    assert receipt["workspace_id"] == before["workspace"]["id"]
    assert receipt["instance_id"] == saved["runtime"]["instance_id"]
    assert receipt["model"] == "synthetic-model" and receipt["at"]
    result = client.post("/api/assistant", json={"message": "Explain current evidence.", "rehearsal": False})
    assert result.status_code == 200, result.text
    explanation = result.json()
    assert explanation["mode"] == "model"
    assert explanation["usage"]["provider_calls"] == 2 and explanation["usage"]["tool_calls"] == 1
    assert explanation["evidence"][0]["data"] == before["summary"]
    assert explanation["proposals"] == []
    assert adapter.connection_calls == 1 and adapter.assistant_calls == 2
    after = refresh_scope(client)
    assert after["workspace"] == before["workspace"]
    assert after["session"] == before["session"]
    status = client.get("/api/settings").json()
    assert status["model_test"] == receipt
    params = {
        "workspace_id": after["workspace"]["id"], "session_id": after["session"]["id"],
        "input_version": after["session"]["input_version"], "revision": after["workspace"]["version"],
    }
    backup = client.get("/api/export/json?" + urlencode(params))
    assert backup.status_code == 200
    for public in (tested.text, result.text, json.dumps(status), backup.text):
        assert SECRET not in public and REPLACEMENT not in public
    for path in tmp_path.iterdir():
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()
    runs = [json.loads(line) for line in (tmp_path / "assistant-runs.jsonl").read_text().splitlines()]
    assert runs[-1]["status"] == "succeeded" and runs[-1]["mode"] == "model"


@pytest.mark.parametrize("invalid", [
    {"status": "incomplete", "output": []},
    {"status": "incomplete", "output": completed_text()["output"]},
    {"status": "failed", "output": completed_text()["output"]},
    {"output": completed_text()["output"]},
    {"status": "completed", "output": []},
    completed_text("Connection might be available."),
    {**completed_text(), "error": {"message": SECRET}},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
])
def test_incomplete_empty_failed_or_unexpected_test_never_leaves_success_receipt(client, invalid):
    before = refresh_scope(client)
    save_model(client)
    client.app.state.common.adapter_factory = lambda bundle: FixedAdapter()
    assert client.post("/api/settings/model/test", json={}).json()["model_test"]
    client.app.state.common.adapter_factory = lambda bundle: FixedAdapter(invalid)
    response = client.post("/api/settings/model/test", json={})
    assert response.status_code == 400
    assert "No successful test was recorded" in response.json()["detail"]
    assert SECRET not in response.text
    status = client.get("/api/settings").json()
    assert status["model_test"] is None and status["model"]["configured"]
    assert refresh_scope(client)["workspace"] == before["workspace"]


def test_workspace_switch_during_connection_test_cannot_record_success_in_either_workspace(client):
    original = refresh_scope(client)
    save_model(client)
    second = client.app.state.store.create("Other synthetic workspace", "USD", True)
    client.app.state.common.settings.register_new_workspace(second["id"])
    client.app.state.store.select(original["workspace"]["id"])
    client.app.state.common.adapter_factory = lambda bundle: FixedAdapter(
        on_respond=lambda: client.app.state.store.select(second["id"])
    )
    response = client.post("/api/settings/model/test", json={})
    assert response.status_code == 409 and "changed during the test" in response.json()["detail"]
    for workspace_id, configured in ((second["id"], False), (original["workspace"]["id"], True)):
        client.app.state.store.select(workspace_id)
        refresh_scope(client)
        status = client.get("/api/settings").json()
        assert status["model_test"] is None and status["model"]["configured"] is configured


def test_key_replacement_during_test_and_later_replacement_or_removal_clear_receipt(client):
    original = refresh_scope(client)
    save_model(client)
    settings = client.app.state.common.settings
    client.app.state.common.adapter_factory = lambda bundle: FixedAdapter(on_respond=lambda: settings.set_model(
        original["workspace"]["id"],
        {"api_key": REPLACEMENT, "model": "synthetic-model", "consent": True, "persist": False},
    ))
    response = client.post("/api/settings/model/test", json={})
    assert response.status_code == 409 and "changed during the test" in response.json()["detail"]
    assert client.get("/api/settings").json()["model_test"] is None
    client.app.state.common.adapter_factory = lambda bundle: FixedAdapter()
    assert client.post("/api/settings/model/test", json={}).json()["model_test"]
    assert save_model(client)["model_test"] is None
    assert client.post("/api/settings/model/test", json={}).json()["model_test"]
    removed = client.delete("/api/settings/model")
    assert removed.status_code == 200
    assert removed.json()["model_test"] is None and not removed.json()["model"]["configured"]
    assert SECRET not in removed.text and REPLACEMENT not in removed.text
    assert refresh_scope(client)["workspace"] == original["workspace"]


def test_concurrent_connection_check_is_rejected_and_failed_check_releases_guard(client):
    before = refresh_scope(client)
    save_model(client)
    provider_calls, nested_responses = [], []

    def reenter_once():
        provider_calls.append("synthetic-provider-call")
        if len(provider_calls) == 1:
            nested_responses.append(client.post("/api/settings/model/test", json={}))

    adapter = FixedAdapter({"status": "incomplete", "output": []}, on_respond=reenter_once)
    client.app.state.common.adapter_factory = lambda bundle: adapter
    failed = client.post("/api/settings/model/test", json={})
    assert len(nested_responses) == 1
    assert nested_responses[0].status_code == 409
    assert "already running for this workspace" in nested_responses[0].json()["detail"]
    assert len(provider_calls) == 1
    assert failed.status_code == 400 and "No successful test was recorded" in failed.json()["detail"]
    assert client.get("/api/settings").json()["model_test"] is None

    adapter.response = completed_text()
    retried = client.post("/api/settings/model/test", json={})
    assert retried.status_code == 200, retried.text
    assert retried.json()["model_test"]["workspace_id"] == before["workspace"]["id"]
    assert len(provider_calls) == 2
    assert refresh_scope(client)["workspace"] == before["workspace"]


def test_new_demo_reproduces_missing_connection_and_original_workspace_recovers_it(client):
    original = refresh_scope(client)
    save_model(client)
    client.app.state.common.adapter_factory = lambda bundle: FixedAdapter()
    receipt = client.post("/api/settings/model/test", json={}).json()["model_test"]
    assert client.post("/api/demo", json={}).status_code == 200
    second = refresh_scope(client)
    assert second["workspace"]["id"] != original["workspace"]["id"]
    assert second["workspace"]["name"] == original["workspace"]["name"]
    status = client.get("/api/settings").json()
    assert not status["model"]["configured"] and not status["model"]["consent"]
    assert status["model_test"] is None
    response = client.post("/api/assistant", json={"message": "Explain evidence.", "rehearsal": False})
    assert response.status_code == 400 and "Set up an AI API key" in response.json()["detail"]
    assert client.post("/api/workspaces/select", json={"workspace_id": original["workspace"]["id"]}).status_code == 200
    recovered = refresh_scope(client)
    status = client.get("/api/settings").json()
    assert status["model"]["configured"] and status["model"]["consent"]
    assert status["model_test"] == receipt
    assert recovered["workspace"] == original["workspace"]
    adapter = ScopedExplanationAdapter(original["summary"])
    client.app.state.common.adapter_factory = lambda bundle: adapter
    result = client.post("/api/assistant", json={"message": "Explain evidence.", "rehearsal": False})
    assert result.status_code == 200 and result.json()["mode"] == "model"


def test_restart_loses_only_session_connection_and_reports_distinct_runtime_identity(client, tmp_path):
    original = refresh_scope(client)
    save_model(client)
    client.app.state.common.adapter_factory = lambda bundle: FixedAdapter()
    tested = client.post("/api/settings/model/test", json={}).json()
    with open_client(create_app(tmp_path, testing=True)) as restarted:
        after = refresh_scope(restarted)
        assert after["workspace"] == original["workspace"]
        assert after["session"] == original["session"]
        status = restarted.get("/api/settings").json()
        assert status["runtime"]["instance_id"] != tested["runtime"]["instance_id"]
        assert status["runtime"]["data_dir"] == tested["runtime"]["data_dir"] == str(tmp_path.resolve())
        assert not status["model"]["configured"] and not status["model"]["consent"]
        assert status["model_test"] is None
        response = restarted.post("/api/assistant", json={"message": "Explain evidence.", "rehearsal": False})
        assert response.status_code == 400 and "Set up an AI API key" in response.json()["detail"]
    assert client.get("/api/settings").json()["model"]["configured"]


def test_runtime_source_and_data_identity_are_consistent_before_and_after_workspace_selection(tmp_path):
    data_dir = tmp_path / "synthetic-data"
    with open_client(create_app(data_dir, testing=True)) as client:
        bootstrap = client.get("/api/bootstrap").json()
        empty = client.get("/api/settings").json()
        assert empty["workspace_required"] and empty["scope"] is None
        runtime = bootstrap["runtime"]
        assert runtime == empty["runtime"]
        assert runtime["source_dir"] == str(Path(vesper_bank_rec.__file__).resolve().parent.parent)
        assert runtime["data_dir"] == str(data_dir.resolve())
        assert runtime["port"] == 8765 and runtime["testing"] is True
        assert len(runtime["instance_id"]) == 32
        assert client.post("/api/demo", json={}).status_code == 200
        state = refresh_scope(client)
        populated = client.get("/api/settings").json()
        assert populated["runtime"] == runtime and populated["scope"] == state["scope"]
        assert SECRET not in json.dumps(populated)
