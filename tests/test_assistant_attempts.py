"""A replay or interrupted local request cannot silently make a second paid call."""

from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from fastapi.testclient import TestClient

from vesper_bank_rec.app import create_app
from vesper_bank_rec.assistant import AssistantError
from vesper_bank_rec.assistant.attempts import AssistantAttempts


def test_receipt_survives_restart_replays_result_and_binds_scope_mode_and_message(tmp_path):
    attempts = AssistantAttempts(tmp_path)
    scope = {"id": "workspace_synthetic", "version": 3, "session_id": "session_synthetic", "input_version": 2}
    request_id = "synthetic_request_id"
    assert attempts.claim(request_id, scope, "Explain the evidence", False) is None
    result = {"message": "Source controls require review.", "evidence": [], "usage": {"provider_calls": 2}}
    attempts.finish(request_id, "succeeded", result)
    resumed = AssistantAttempts(tmp_path)
    assert resumed.claim(request_id, scope, "Explain the evidence", False) == result
    for changed, message, mode in [
        ({**scope, "id": "workspace_other"}, "Explain the evidence", False),
        ({**scope, "version": 4}, "Explain the evidence", False),
        (scope, "A different question", False), (scope, "Explain the evidence", True),
    ]:
        with pytest.raises(AssistantError, match="different work"):
            resumed.claim(request_id, changed, message, mode)


@pytest.mark.parametrize("status", ["started", "failed", "cancelled"])
def test_uncertain_or_failed_attempt_requires_an_explicit_new_request(tmp_path, status):
    attempts = AssistantAttempts(tmp_path)
    scope = {"id": "workspace_synthetic"}
    attempts.claim("synthetic_request_id", scope, "Question", False)
    if status != "started":
        attempts.finish("synthetic_request_id", status)
    with pytest.raises(AssistantError, match="will not be charged again automatically"):
        AssistantAttempts(tmp_path).claim("synthetic_request_id", scope, "Question", False)
    assert attempts.claim("explicit_new_request_id", scope, "Question", False) is None


def test_only_one_concurrent_claim_and_workspace_delete_removes_its_receipts(tmp_path):
    attempts = AssistantAttempts(tmp_path)
    def claim(_):
        try:
            attempts.claim("synthetic_request_id", {"id": "workspace_first"}, "Question", False)
            return True
        except AssistantError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(4))) == 1
    attempts.claim("synthetic_other_request", {"id": "workspace_other"}, "Question", False)
    attempts.delete_workspace("workspace_first")
    assert attempts.claim("synthetic_request_id", {"id": "workspace_first"}, "Question", False) is None
    with pytest.raises(AssistantError):
        attempts.claim("synthetic_other_request", {"id": "workspace_other"}, "Question", False)


def test_http_replay_uses_saved_receipt_without_dispatching_tools_again(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.get("/")
        client.headers.update({"Origin": "http://127.0.0.1:8765", "X-Vesper-CSRF": client.get("/api/bootstrap").json()["csrf_token"]})
        assert client.post("/api/demo", json={}).status_code == 200
        state = client.get("/api/state").json()
        client.headers["X-Vesper-Scope"] = json.dumps(state["scope"])
        body = {"message": "Explain the synthetic records", "rehearsal": True, "request_id": "synthetic_http_request"}
        first = client.post("/api/assistant", json=body)
        assert first.status_code == 200, first.text
        def should_not_run(*_):
            raise AssertionError("A replay must not call the provider")
        app.state.common.adapter_factory = should_not_run
        second = client.post("/api/assistant", json=body)
        assert second.status_code == 200
        assert second.json() == {**first.json(), "replayed": True}
        records = [json.loads(line) for line in (tmp_path / "assistant-runs.jsonl").read_text().splitlines()]
        assert [entry["status"] for entry in records] == ["started", "succeeded"]
        assert records[1]["usage"]["provider_calls"] == 0
        assert not any("message" in entry for entry in records)
