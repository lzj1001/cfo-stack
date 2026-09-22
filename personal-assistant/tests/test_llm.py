import asyncio

from pydantic import BaseModel, ConfigDict, SecretStr
import pytest
import requests

from personal_assistant.errors import LLMUnavailableError
from personal_assistant.llm import RequestsTransport, SiliconFlowClient


class Routed(BaseModel):
    model_config = ConfigDict(strict=True)

    intent: str
    confidence: float


def run(client, messages=None):
    return asyncio.run(
        client.structured(messages or [{"role": "user", "content": "hi"}], Routed, model="router")
    )


def test_malformed_output_is_repaired_once():
    responses = iter(
        [
            {"status": 200, "content": "not json", "usage": None},
            {
                "status": 200,
                "content": '{"intent":"CHAT","confidence":0.9}',
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
        ]
    )
    calls = []

    def post(**kwargs):
        calls.append(kwargs)
        return next(responses)

    result = run(SiliconFlowClient(post=post, max_retries=0))
    assert result.intent == "CHAT"
    assert len(calls) == 2


def test_second_malformed_output_fails_safely():
    calls = []

    def post(**kwargs):
        calls.append(kwargs)
        return {"status": 200, "content": "bad", "usage": None}

    with pytest.raises(LLMUnavailableError, match="structured output"):
        run(SiliconFlowClient(post=post, max_retries=0))
    assert len(calls) == 2


def test_429_retries_once_then_succeeds():
    responses = iter(
        [
            {"status": 429, "content": "", "usage": None},
            {"status": 200, "content": '{"intent":"CHAT","confidence":0.8}', "usage": None},
        ]
    )
    sleeps = []
    client = SiliconFlowClient(
        post=lambda **_: next(responses), max_retries=1, sleep=sleeps.append
    )
    assert run(client).intent == "CHAT"
    assert sleeps == [0.5]


def test_repeated_500_stops_at_retry_limit():
    calls = 0

    def post(**_):
        nonlocal calls
        calls += 1
        return {"status": 500, "content": "", "usage": None}

    with pytest.raises(LLMUnavailableError, match="HTTP 500"):
        run(SiliconFlowClient(post=post, max_retries=2, sleep=lambda _: None))
    assert calls == 3


def test_401_is_not_retried():
    calls = 0

    def post(**_):
        nonlocal calls
        calls += 1
        return {"status": 401, "content": "", "usage": None}

    with pytest.raises(LLMUnavailableError, match="HTTP 401"):
        run(SiliconFlowClient(post=post, max_retries=3, sleep=lambda _: None))
    assert calls == 1


def test_timeout_is_not_retried():
    calls = 0

    def post(**_):
        nonlocal calls
        calls += 1
        raise TimeoutError("late")

    with pytest.raises(LLMUnavailableError, match="timeout"):
        run(SiliconFlowClient(post=post, max_retries=3))
    assert calls == 1


def test_schema_invalid_json_gets_only_one_repair():
    calls = 0

    def post(**_):
        nonlocal calls
        calls += 1
        return {"status": 200, "content": '{"intent":"CHAT","confidence":"high"}', "usage": None}

    with pytest.raises(LLMUnavailableError, match="structured output"):
        run(SiliconFlowClient(post=post, max_retries=0))
    assert calls == 2


@pytest.mark.parametrize(
    "usage,expected",
    [
        ({"prompt_tokens": 10, "completion_tokens": 4}, (10, 4)),
        (None, (None, None)),
    ],
)
def test_metadata_records_usage_without_prompt_or_secret(usage, expected):
    records = []
    client = SiliconFlowClient(
        post=lambda **_: {
            "status": 200,
            "content": '{"intent":"CHAT","confidence":0.9}',
            "usage": usage,
        },
        max_retries=0,
        metadata_sink=lambda **values: records.append(values),
    )
    run(client, [{"role": "user", "content": "private text"}])

    assert (records[0]["input_tokens"], records[0]["output_tokens"]) == expected
    assert records[0]["provider"] == "siliconflow"
    assert records[0]["model"] == "router"
    assert set(records[0]).isdisjoint({"prompt", "messages", "api_key", "secret"})


def test_requests_transport_sends_json_schema_request():
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": '{"intent":"CHAT","confidence":0.9}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            }

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    result = RequestsTransport(
        base_url="https://api.siliconflow.cn/v1/",
        api_key=SecretStr("test-key"),
        timeout_seconds=30,
        post=post,
    )(model="router", messages=[{"role": "user", "content": "hi"}], schema=Routed)

    assert captured["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["response_format"]["json_schema"]["strict"] is True
    assert captured["timeout"] == 30
    assert result["status"] == 200


def test_requests_transport_maps_timeout_without_leaking_key():
    def post(*args, **kwargs):
        raise requests.Timeout("late")

    transport = RequestsTransport(
        base_url="https://api.siliconflow.cn/v1",
        api_key=SecretStr("never-print-this"),
        timeout_seconds=30,
        post=post,
    )
    with pytest.raises(TimeoutError) as caught:
        transport(model="router", messages=[], schema=Routed)
    assert "never-print-this" not in str(caught.value)
