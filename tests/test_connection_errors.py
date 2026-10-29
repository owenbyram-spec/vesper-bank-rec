"""Actionable connection failures must never expose provider bodies or credential values."""

import json

import httpx
import pytest

from vesper_bank_rec.assistant import AssistantError, ResponsesAdapter


SECRET = "synthetic-key-never-used-with-a-live-provider"
MODEL = "synthetic-selected-model-do-not-echo"


@pytest.mark.parametrize("status,code,expected", [
    (401, "invalid_api_key", "Copy an active key"),
    (403, "permission_denied", "Responses API and selected model"),
    (400, "model_not_found", "could not find the selected model"),
    (404, "model_not_found", "Advanced: model choice"),
    (429, "insufficient_quota", "available credits or a higher usage limit"),
    (429, "credit_balance_exhausted", "OpenAI Billing and project limits"),
    (429, "organization_spend_limit_exceeded", "available credits or a higher usage limit"),
    (429, "project_spend_limit_exceeded", "available credits or a higher usage limit"),
    (429, "organization_usage_limit_exceeded", "available credits or a higher usage limit"),
    (429, "usage_limit_exceeded", "available credits or a higher usage limit"),
    (429, "rate_limit_exceeded", "temporarily limiting API requests"),
    (429, "slow_down", "Wait and try again later"),
    (500, "server_error", "temporarily unavailable"),
    (502, None, "temporarily unavailable"),
    (503, "server_is_overloaded", "temporarily unavailable"),
    (408, None, "did not respond in time"),
    (400, "unknown_provider_code", "could not accept this request"),
    (404, "unknown_provider_code", "could not accept this request"),
])
def test_provider_errors_give_safe_next_steps_without_retry_or_model_fallback(status, code, expected):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={
            "error": {"code": code, "message": SECRET + " " + MODEL},
            "credentials": SECRET,
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        adapter = ResponsesAdapter(api_key=SECRET, model=MODEL, client=client)
        with pytest.raises(AssistantError) as error:
            adapter.respond([{"role": "user", "content": "Synthetic connection request"}], [], 3)
    message = str(error.value)
    assert expected in message and "Your reconciliation is saved" in message
    assert SECRET not in message and MODEL not in message and "unknown_provider_code" not in message
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["Authorization"] == "Bearer " + SECRET
    payload = json.loads(request.content)
    assert payload["model"] == MODEL and payload["store"] is False
    assert payload["tools"] == [] and payload["max_output_tokens"] == 2400
    assert request.extensions["timeout"]["read"] == 3


@pytest.mark.parametrize("body", [
    {"error": {"code": SECRET, "message": "model_not_found " + SECRET}},
    {"error": {"code": {"model_not_found": SECRET}, "message": SECRET}},
    {"error": SECRET},
    {"message": "model_not_found " + SECRET},
    [SECRET],
])
def test_unrecognized_provider_values_cannot_select_a_model_error_or_escape_in_messages(body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404, json=body))) as client:
        with pytest.raises(AssistantError) as error:
            ResponsesAdapter(api_key=SECRET, model=MODEL, client=client).respond([], [])
    assert "could not accept this request" in str(error.value)
    assert "could not find the selected model" not in str(error.value)
    assert SECRET not in str(error.value)


def test_known_quota_error_type_with_an_unknown_code_still_gives_billing_action():
    body = {"error": {"type": "insufficient_quota", "code": SECRET, "message": SECRET}}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, json=body))) as client:
        with pytest.raises(AssistantError, match="OpenAI Billing and project limits") as error:
            ResponsesAdapter(api_key=SECRET, model=MODEL, client=client).respond([], [])
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("status,body,expected", [
    (429, b"not-json-secret-" + SECRET.encode(), "temporarily limiting API requests"),
    (404, b"not-json-secret-" + SECRET.encode(), "could not accept this request"),
    (200, b"not-json-secret-" + SECRET.encode(), "response the app could not read"),
    (200, json.dumps({"output": SECRET}).encode(), "response the app could not read"),
    (400, b"x" * 600001, "response exceeded the local safety limit"),
])
def test_malformed_and_oversized_responses_remain_bounded_and_do_not_echo_body(status, body, expected):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body))) as client:
        with pytest.raises(AssistantError, match=expected) as error:
            ResponsesAdapter(api_key=SECRET, model=MODEL, client=client).respond([], [])
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("failure,expected", [
    (httpx.ConnectError, "Check your internet connection"),
    (httpx.ReadError, "ask your IT team"),
    (httpx.ConnectTimeout, "did not respond in time"),
    (httpx.ReadTimeout, "Wait a moment and try again"),
])
def test_offline_and_timeout_failures_have_distinct_safe_actions(failure, expected):
    requests = []

    def respond(request):
        requests.append(request)
        raise failure(SECRET, request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AssistantError) as error:
            ResponsesAdapter(api_key=SECRET, model=MODEL, client=client).respond([], [])
    assert expected in str(error.value) and "Your reconciliation is saved" in str(error.value)
    assert SECRET not in str(error.value) and error.value.__suppress_context__ is True
    assert len(requests) == 1


def test_owned_provider_client_keeps_network_controls_and_closes_after_failure(monkeypatch):
    real_client, owned = httpx.Client, []

    def client_factory(**options):
        client = real_client(
            **options, transport=httpx.MockTransport(lambda request: httpx.Response(503, json={"error": SECRET}))
        )
        owned.append((client, options))
        return client

    monkeypatch.setattr("vesper_bank_rec.assistant.httpx.Client", client_factory)
    with pytest.raises(AssistantError, match="temporarily unavailable"):
        ResponsesAdapter(api_key=SECRET, model=MODEL).respond([], [], 35)
    assert len(owned) == 1
    client, options = owned[0]
    assert options == {"timeout": 20, "follow_redirects": False, "trust_env": False}
    assert client.is_closed
