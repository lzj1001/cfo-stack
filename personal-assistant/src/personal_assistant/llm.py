from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, SecretStr, ValidationError as PydanticValidationError
import requests

from .errors import LLMUnavailableError


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMClient(Protocol):
    async def structured(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
        *,
        model: str,
    ) -> SchemaT: ...


class SiliconFlowClient:
    def __init__(
        self,
        *,
        post: Callable[..., dict],
        max_retries: int,
        sleep: Callable[[float], None] = time.sleep,
        metadata_sink: Callable[..., None] | None = None,
    ) -> None:
        self._post = post
        self._max_retries = max_retries
        self._sleep = sleep
        self._metadata_sink = metadata_sink

    async def structured(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
        *,
        model: str,
    ) -> SchemaT:
        started = time.monotonic()
        usage: dict | None = None
        result = "failed"
        current_messages = list(messages)
        try:
            for repair_attempt in range(2):
                response = await asyncio.to_thread(
                    self._post_with_retry,
                    model=model,
                    messages=current_messages,
                    schema=schema,
                )
                usage = response.get("usage")
                try:
                    parsed = json.loads(str(response.get("content") or ""))
                    validated = schema.model_validate(parsed)
                    result = "success"
                    return validated
                except (json.JSONDecodeError, PydanticValidationError, TypeError) as error:
                    if repair_attempt == 1:
                        raise LLMUnavailableError("structured output validation failed") from error
                    current_messages = [
                        *messages,
                        {
                            "role": "system",
                            "content": (
                                "Return only valid JSON matching the supplied schema. "
                                f"Previous response error: {_validation_summary(error)}."
                            ),
                        },
                    ]
            raise LLMUnavailableError("structured output validation failed")
        except TimeoutError as error:
            raise LLMUnavailableError("model timeout") from error
        finally:
            if self._metadata_sink is not None:
                self._metadata_sink(
                    call_id=f"llm_{uuid4().hex}",
                    provider="siliconflow",
                    model=model,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                    input_tokens=_usage_value(usage, "prompt_tokens"),
                    output_tokens=_usage_value(usage, "completion_tokens"),
                    result=result,
                )

    def _post_with_retry(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> dict:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._post(model=model, messages=messages, schema=schema)
            except TimeoutError:
                raise
            except Exception as error:
                raise LLMUnavailableError("model transport failed") from error

            status = int(response.get("status", 0))
            if status == 200:
                return response
            retryable = status == 429 or 500 <= status <= 599
            if not retryable or attempt == self._max_retries:
                raise LLMUnavailableError(f"model HTTP {status}")
            self._sleep(0.5 * (2**attempt))
        raise LLMUnavailableError("model retry limit reached")


class RequestsTransport:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        timeout_seconds: int,
        post: Callable[..., object] = requests.post,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._post = post

    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> dict:
        try:
            response = self._post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "strict": True,
                            "schema": schema.model_json_schema(),
                        },
                    },
                },
                timeout=self._timeout,
            )
        except requests.Timeout as error:
            raise TimeoutError("model timeout") from error

        status = int(response.status_code)
        if status != 200:
            return {"status": status, "content": "", "usage": None}
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMUnavailableError("model response shape invalid") from error
        return {"status": status, "content": content, "usage": body.get("usage")}


def _validation_summary(error: Exception) -> str:
    if isinstance(error, json.JSONDecodeError):
        return "invalid JSON"
    if isinstance(error, PydanticValidationError):
        first = error.errors(include_input=False)[0]
        location = ".".join(str(part) for part in first.get("loc", ())) or "root"
        return f"{location}: {first.get('type', 'schema error')}"
    return error.__class__.__name__


def _usage_value(usage: dict | None, key: str) -> int | None:
    if not isinstance(usage, dict) or usage.get(key) is None:
        return None
    return int(usage[key])
