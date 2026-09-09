"""Talking to the box, and the four things that must not go wrong.

Nothing here touches the network. Every request goes through an
``httpx.MockTransport``, so the suite runs on a plane and a broken box never
turns into a failing test.

The four:

- **Failure kinds stay distinct.** A 401 is terminal and a sleeping Mac is not,
  and collapsing them into one "offline" state is how a rotated key goes
  uninvestigated for a week.
- **The address is checked before the key is attached**, so a guard that failed
  open would still not put the key on the wire.
- **The server's model identity is checked every time**, because a box swapped
  under the client would silently corrupt provenance.
- **The key reaches nothing that gets written down.**
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.errors import (
    AuthRejected,
    EndpointNotPrivate,
    EndpointUnreachable,
    InferenceError,
    ModelIdentityMismatch,
    RateLimited,
)
from agent.llm import client as client_mod
from agent.llm import credentials, redaction
from agent.llm.settings import AuthSettings, VlmSettings
from agent.llm.endpoint import parse as parse_endpoint

KEY = "sk-box-9f3a2b1c8d7e6f5a4b3c"
MODEL = "Qwen3.8-Flash-Next-oQ4e-mtp"
PRIVATE = ("100.94.135.1",)


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch):
    monkeypatch.setenv("HEALTH_VLM_TOKEN_TEST", KEY)
    monkeypatch.setattr(credentials, "_from_keychain", lambda: None)
    redaction.forget_all()
    yield
    redaction.forget_all()


def _settings(**overrides) -> VlmSettings:
    base = {
        "endpoint": parse_endpoint("https://box.tailnet.ts.net/v1"),
        "model": MODEL,
        "auth": AuthSettings(api_key_env="HEALTH_VLM_TOKEN_TEST"),
    }
    return VlmSettings(**{**base, **overrides})


def _answer(content="{}", model=MODEL, **extra):
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 40},
        **extra,
    }


def _client(handler, resolver=lambda host, port=None: list(PRIVATE), **overrides):
    return client_mod.Client(
        _settings(**overrides),
        transport=httpx.MockTransport(handler),
        resolver=resolver,
    )


def _ok(content="{}", model=MODEL):
    def handler(request):
        return httpx.Response(200, json=_answer(content, model))

    return handler


MESSAGES = [{"role": "user", "content": "read this"}]


# --- failure kinds ---------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_failure_is_terminal_and_says_so(status):
    """Not a network problem, and never retried."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "invalid api key"})

    with _client(handler) as client:
        with pytest.raises(AuthRejected) as raised:
            client.complete(MESSAGES)

    message = str(raised.value)
    assert "authentication rejected" in message
    assert "the key may have rotated" in message
    assert "not retried" in message
    assert len(calls) == 1, "one attempt; retrying a rotated key can trip lockout"


def test_a_rate_limit_is_its_own_state_and_is_worth_logging():
    with _client(lambda r: httpx.Response(429)) as client:
        with pytest.raises(RateLimited) as raised:
            client.complete(MESSAGES)

    assert "something else is hammering it" in str(raised.value)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_is_transient_not_terminal(status):
    with _client(lambda r: httpx.Response(status)) as client:
        with pytest.raises(EndpointUnreachable, match="stays queued"):
            client.complete(MESSAGES)


def test_a_refused_socket_is_unreachable_not_unauthorised():
    """The box is asleep. Retry silently, drain later — nobody needs telling."""

    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    with _client(handler) as client:
        with pytest.raises(EndpointUnreachable) as raised:
            client.complete(MESSAGES)

    assert not isinstance(raised.value, AuthRejected)
    assert "will drain when the box is back" in str(raised.value)


def test_a_timeout_is_unreachable():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with _client(handler) as client:
        with pytest.raises(EndpointUnreachable, match="timed out"):
            client.complete(MESSAGES)


def test_a_client_error_that_is_not_auth_is_a_plain_refusal():
    with _client(lambda r: httpx.Response(400, text="bad schema")) as client:
        with pytest.raises(InferenceError) as raised:
            client.complete(MESSAGES)

    assert not isinstance(raised.value, (AuthRejected, EndpointUnreachable))
    assert "bad schema" in str(raised.value)


# --- the guard runs before the key is attached -----------------------------


def test_no_request_is_made_when_the_host_resolves_publicly():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_answer())

    with _client(handler, resolver=lambda h, p=None: ["203.0.113.7"]) as client:
        with pytest.raises(EndpointNotPrivate):
            client.complete(MESSAGES)

    assert calls == [], "the guard runs before anything is sent"


def test_the_credential_is_not_even_resolved_for_a_public_host(monkeypatch):
    """Ordering, so the key does not leave the machine if the guard fails open."""
    asked = []
    monkeypatch.setattr(
        credentials,
        "resolve",
        lambda *args, **kwargs: asked.append(args) or credentials.Credential(KEY, "test"),
    )
    monkeypatch.setattr(client_mod.credentials_mod, "resolve", credentials.resolve)

    with _client(lambda r: httpx.Response(200), resolver=lambda h, p=None: ["8.8.8.8"]) as client:
        with pytest.raises(EndpointNotPrivate):
            client.complete(MESSAGES)

    assert asked == [], "the address is checked first, so the key is never read"


def test_headers_refuse_to_be_built_without_verified_addresses():
    """The belt-and-braces half: `_headers` re-checks what it is handed."""
    with _client(_ok()) as client:
        with pytest.raises(InferenceError, match="has not been verified as private"):
            client._headers(["203.0.113.7"])
        with pytest.raises(InferenceError):
            client._headers([])


def test_the_configured_header_shape_is_used():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json=_answer())

    settings = _settings(
        auth=AuthSettings(api_key_env="HEALTH_VLM_TOKEN_TEST", header="X-API-Key", scheme="")
    )
    with client_mod.Client(
        settings,
        transport=httpx.MockTransport(handler),
        resolver=lambda h, p=None: list(PRIVATE),
    ) as client:
        client.complete(MESSAGES)

    assert seen["x-api-key"] == KEY
    assert "authorization" not in seen


def test_the_default_header_shape_is_bearer():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json=_answer())

    with _client(handler) as client:
        client.complete(MESSAGES)

    assert seen["authorization"] == f"Bearer {KEY}"


# --- model identity --------------------------------------------------------


def test_a_swapped_model_stops_the_run():
    with _client(_ok(model="some-other-model")) as client:
        with pytest.raises(ModelIdentityMismatch) as raised:
            client.complete(MESSAGES)

    message = str(raised.value)
    assert "some-other-model" in message
    assert MODEL in message
    assert "never updated automatically" in message


def test_a_server_that_does_not_name_the_model_stops_the_run():
    def handler(request):
        answer = _answer()
        answer.pop("model")
        return httpx.Response(200, json=answer)

    with _client(handler) as client:
        with pytest.raises(ModelIdentityMismatch, match="did not say which model"):
            client.complete(MESSAGES)


def test_the_reported_identity_is_carried_on_the_completion():
    with _client(_ok()) as client:
        answer = client.complete(MESSAGES)

    assert answer.model == MODEL
    assert answer.prompt_tokens == 1200


# --- the request body ------------------------------------------------------


def test_a_schema_is_sent_as_strict_guided_grammar():
    """Without it these models narrate a plan and every extraction fails."""
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_answer())

    with _client(handler) as client:
        client.complete(MESSAGES, schema={"type": "object"}, schema_name="x")

    fmt = bodies[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "x"


def test_sampling_is_pinned_for_reproducibility_not_prose():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_answer())

    with _client(handler) as client:
        client.complete(MESSAGES)

    body = bodies[0]
    assert body["temperature"] == 0.0
    assert body["presence_penalty"] == 0.0, "penalising repeated tokens fights the grammar"
    assert body["max_pixels"] == 1638400, "set explicitly, not left to server defaults"
    assert body["stream"] is False


def test_thinking_is_off_by_default_and_retried_once_if_the_server_objects():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "chat_template_kwargs" in body:
            return httpx.Response(400, text="unknown field: chat_template_kwargs")
        return httpx.Response(200, json=_answer())

    with _client(handler) as client:
        client.complete(MESSAGES)

    assert len(bodies) == 2
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in bodies[1]


def test_a_real_four_hundred_is_not_retried_away():
    """The retry is narrow on purpose: a bad schema must fail, not loop."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, text="your schema has a cycle in it")

    with _client(handler) as client:
        with pytest.raises(InferenceError, match="cycle"):
            client.complete(MESSAGES)

    assert len(calls) == 1


def test_thinking_on_sends_no_toggle():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_answer())

    with _client(handler, thinking=True) as client:
        client.complete(MESSAGES)

    assert "chat_template_kwargs" not in bodies[0]


# --- reasoning traces ------------------------------------------------------


def test_a_reasoning_trace_is_stripped_and_the_fact_recorded():
    """Reported rather than silent: it means the server has no reasoning parser."""
    content = "<think>The user wants the dose. Let me look.</think>\n{\"dose\": \"5mg\"}"
    with _client(_ok(content)) as client:
        answer = client.complete(MESSAGES)

    assert answer.content == '{"dose": "5mg"}'
    assert answer.stripped_reasoning is True


def test_ordinary_content_is_not_marked_as_stripped():
    with _client(_ok('{"dose": "5mg"}')) as client:
        assert client.complete(MESSAGES).stripped_reasoning is False


def test_a_narrated_plan_is_not_quietly_turned_into_json():
    """MODELS.md: do not post-process free text into JSON with a regex."""
    with pytest.raises(InferenceError, match="not JSON"):
        client_mod.parse_json_content("I'll start by reading the header. {\"dose\": 1}")


# --- the key reaches nothing -----------------------------------------------


def test_the_key_never_appears_in_an_error_message():
    """httpx puts request headers into some error representations."""

    def handler(request):
        raise httpx.ConnectError(
            f"failed sending headers Authorization: Bearer {KEY}", request=request
        )

    with _client(handler) as client:
        with pytest.raises(EndpointUnreachable) as raised:
            client.complete(MESSAGES)

    assert KEY not in str(raised.value)
    assert redaction.REDACTED in str(raised.value)


def test_the_key_never_appears_in_a_refusal_body():
    """A server that echoes the header back must not get it into our logs."""

    def handler(request):
        return httpx.Response(400, text=f"rejected request with Bearer {KEY}")

    with _client(handler) as client:
        with pytest.raises(InferenceError) as raised:
            client.complete(MESSAGES)

    assert KEY not in str(raised.value)


def test_the_completion_object_carries_no_credential():
    with _client(_ok()) as client:
        answer = client.complete(MESSAGES)

    assert KEY not in json.dumps(answer.raw)
    assert KEY not in repr(answer)


# --- the vision probe ------------------------------------------------------


def test_the_vision_probe_passes_when_the_answer_contains_the_known_text():
    with _client(_ok("PERINDOPRIL 5MG")) as client:
        assert client.probe_vision("image/jpeg", "AAAA", "perindopril") is True


def test_the_vision_probe_fails_when_the_image_was_silently_discarded():
    """The failure it exists for: a text-only server that ignores image parts.

    It answers text prompts perfectly and quietly drops every prescription
    photo, which presents as "the model is bad at OCR".
    """
    with _client(_ok("I cannot see any image in this conversation.")) as client:
        assert client.probe_vision("image/jpeg", "AAAA", "perindopril") is False


def test_an_image_part_is_a_data_uri():
    part = client_mod.image_part("image/jpeg", "QUJD")
    assert part["image_url"]["url"] == "data:image/jpeg;base64,QUJD"
