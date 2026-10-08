import json

import httpx
import pytest
from pydantic import ValidationError

from jev_decide_mcp.client import BackendError, DecideClient, Settings, endpoint_url
from jev_decide_mcp.contract import ContractError, DecideRequest, validate_decision


def decision(body):
    kind = body["kind"]
    opts = (
        body.get("options")
        if kind == "choice"
        else (["false", "true"] if kind == "noul" else [str(i) for i in range(6)])
    )
    data = {
        "kind": kind,
        "effective_kind": kind,
        "options": opts,
        "probabilities": [0.0] * (len(opts) - 1) + [1.0],
        "choice_index": len(opts) - 1,
        "choice": opts[-1],
        "protocol": "jev27-bare-v1",
        "adaptation": "native",
        "model": "self-hosted-name",
        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        "elapsed_seconds": 0.0123,
        "num_model_requests": 1,
        "future_field": {"preserve": True},
    }
    if body.get("system2_only"):
        for key in ("protocol", "effective_kind", "adaptation"):
            del data[key]
        del data["usage"]["total_tokens"]
        data["system"] = 2
    return data


@pytest.mark.parametrize(
    "state",
    [
        "한글 evidence",
        {"log": "실패", "count": 3},
        ["text", {"image": "data:image/png;base64,AA=="}],
    ],
)
@pytest.mark.parametrize("kind", ["choice", "noul", "score"])
async def test_exact_body_and_response(state, kind):
    body = {"kind": kind, "state": state, "question": "질문?"}
    if kind == "choice":
        body["options"] = ["one", "two", "one"]  # duplicate labels must not collapse
    expected = decision(body)

    def backend(req):
        assert str(req.url) == "http://backend/gateway/v1/decide"
        assert json.loads(req.content) == body
        assert req.headers["authorization"] == "Bearer local-key"
        return httpx.Response(200, json=expected)

    async with DecideClient(
        Settings("http://backend/gateway/v1", api_key="local-key"),
        transport=httpx.MockTransport(backend),
    ) as client:
        assert await client.decide(DecideRequest.model_validate(body)) == expected


async def test_all_controls_and_explicit_null_are_preserved():
    body = {
        "kind": "choice",
        "question": "q",
        "state": {"a": [1, True, None]},
        "options": ["x", "y"],
        "strategy": "permute",
        "thinking": "auto",
        "threshold": 0.8,
        "think_budget": 128,
        "reasoning_effort": "low",
        "chat_template_kwargs": {
            "reasoning_effort": "medium",
            "nested": {"keep": True},
        },
        "return_reasoning": True,
        "debug": True,
        "system2_only": False,
    }
    response = decision(body)
    response["thinking"] = {
        "used": True,
        "reasoning": "preserve",
        "system1": [0.2, 0.8],
    }
    seen = []

    def backend(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=response)

    async with DecideClient(
        Settings("http://backend"), transport=httpx.MockTransport(backend)
    ) as c:
        assert await c.decide(DecideRequest.model_validate(body)) == response
        body.update(
            threshold=None,
            think_budget=None,
            reasoning_effort=None,
            chat_template_kwargs=None,
        )
        await c.decide(DecideRequest.model_validate(body))
    assert seen == [
        dict(
            body,
            threshold=0.8,
            think_budget=128,
            reasoning_effort="low",
            chat_template_kwargs={
                "reasoning_effort": "medium",
                "nested": {"keep": True},
            },
        ),
        body,
    ]


async def test_system2_response_does_not_require_or_invent_system1_fields():
    body = {"kind": "noul", "question": "q", "system2_only": True}
    expected = decision(body)
    async with DecideClient(
        Settings("http://backend"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=expected)),
    ) as client:
        assert await client.decide(DecideRequest.model_validate(body)) == expected


@pytest.mark.parametrize("n", [2, 16, 17, 128, 256])
def test_choice_sizes(n):
    body = {"kind": "choice", "question": "q", "options": [str(i) for i in range(n)]}
    validate_decision(decision(body), DecideRequest.model_validate(body))


@pytest.mark.parametrize("n", [0, 1, 257])
def test_invalid_choice_sizes(n):
    with pytest.raises(ValidationError):
        DecideRequest(kind="choice", question="q", options=["x"] * n)


@pytest.mark.parametrize(
    "patch",
    [
        {"kind": "rank"},
        {"model": "cloud"},
        {"think_budget": True},
        {"threshold": float("nan")},
        {"state": None},
    ],
)
def test_strict_invalid_arguments(patch):
    with pytest.raises(ValidationError):
        DecideRequest.model_validate({"kind": "noul", "question": "q", **patch})


@pytest.mark.parametrize(
    "patch",
    [
        {"probabilities": [0.5]},
        {"probabilities": [0.2, 0.2]},
        {"probabilities": [-0.1, 1.1]},
        {"probabilities": [float("nan"), 1]},
        {"probabilities": [True, 0]},
        {"options": ["true", "false"]},
        {"choice_index": True},
        {"choice_index": 2},
        {"choice": "false"},
        {"kind": "choice"},
        {"protocol": "systemone"},
    ],
)
def test_broken_decisions_rejected(patch):
    body = {"kind": "noul", "question": "q"}
    with pytest.raises(ContractError):
        validate_decision(
            {**decision(body), **patch}, DecideRequest.model_validate(body)
        )


def test_tie_and_rounded_distribution():
    body = {"kind": "noul", "question": "q"}
    data = {
        **decision(body),
        "probabilities": [0.5, 0.5],
        "choice_index": 0,
        "choice": "false",
    }
    validate_decision(data, DecideRequest.model_validate(body))
    with pytest.raises(ContractError):
        validate_decision(
            {**data, "choice_index": 1, "choice": "true"},
            DecideRequest.model_validate(body),
        )
    data.update(probabilities=[0.4999, 0.4998])
    validate_decision(data, DecideRequest.model_validate(body))


@pytest.mark.parametrize("status", [301, 400, 401, 404, 422, 500, 503])
async def test_http_errors_and_redirects_are_not_retried(status):
    calls = []
    detail = {"error": {"message": "backend error", "code": status}}

    def backend(req):
        calls.append(req)
        return httpx.Response(
            status, json=detail, headers={"location": "http://other-host"}
        )

    async with DecideClient(
        Settings("http://backend"), transport=httpx.MockTransport(backend)
    ) as c:
        with pytest.raises(BackendError) as err:
            await c.decide(DecideRequest(kind="noul", question="q"))
    assert err.value.status == status
    assert err.value.detail == detail
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="broken"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"error": {"message": "oops"}}),
    ],
)
async def test_non_decision_success_bodies_fail(response):
    async with DecideClient(
        Settings("http://backend"), transport=httpx.MockTransport(lambda r: response)
    ) as c:
        with pytest.raises(BackendError):
            await c.decide(DecideRequest(kind="noul", question="q"))


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("key-secret"), httpx.ReadTimeout("key-secret")]
)
async def test_network_errors_do_not_expose_transport_details(exc):
    def backend(req):
        raise exc

    async with DecideClient(
        Settings("http://backend"), transport=httpx.MockTransport(backend)
    ) as c:
        with pytest.raises(BackendError) as err:
            await c.info()
    assert "key-secret" not in str(err.value)


async def test_total_deadline_and_cancellation():
    import asyncio

    cancelled = asyncio.Event()

    async def backend(req):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    async with DecideClient(
        Settings("http://backend", timeout_seconds=0.01),
        transport=httpx.MockTransport(backend),
    ) as c:
        with pytest.raises(BackendError, match="exceeded"):
            await c.info()
    assert cancelled.is_set()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://host", "http://host/v1/decide"),
        ("http://host/prefix/", "http://host/prefix/v1/decide"),
        ("http://host/prefix/v1/", "http://host/prefix/v1/decide"),
        ("http://host/prefix/v1/decide/", "http://host/prefix/v1/decide"),
    ],
)
def test_url_normalization(url, expected):
    assert endpoint_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host",
        "host:8000",
        "https://key@host",
        "https://host?a=1",
        "https://host#frag",
    ],
)
def test_unsafe_url_forms_rejected(url):
    with pytest.raises(ValueError):
        Settings(url)


def test_backend_configuration_required_and_secrets_not_in_repr(monkeypatch):
    monkeypatch.delenv("JEV_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="required"):
        Settings.from_env()
    assert "secret" not in repr(Settings("http://backend", api_key="secret"))


async def test_info_exact_path_and_response():
    expected = {
        "protocol": "jev27-bare-v1",
        "max_options": 256,
        "defaults": {"thinking": "off"},
        "new": [1],
    }

    def backend(req):
        assert req.method == "GET"
        assert req.url.path == "/v1/decide/info"
        return httpx.Response(200, json=expected)

    async with DecideClient(
        Settings("http://backend/v1/decide"), transport=httpx.MockTransport(backend)
    ) as c:
        assert await c.info() == expected
