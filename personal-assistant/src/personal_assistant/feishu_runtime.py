from __future__ import annotations

import time
from typing import Callable
from decimal import Decimal

from pydantic import SecretStr
import requests

from .errors import FeishuWriteError


FEISHU_API = "https://open.feishu.cn/open-apis"


class FeishuOpenAPITransport:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: SecretStr,
        request: Callable[..., object] = requests.request,
        now: Callable[[], float] = time.time,
        timeout_seconds: int = 30,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._request = request
        self._now = now
        self._timeout = timeout_seconds
        self._token = ""
        self._token_expires_at = 0.0

    def __call__(
        self, method: str, path: str, json: dict | None = None
    ) -> dict:
        token = self._tenant_token()
        items: list[dict] = []
        page_token = ""
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            try:
                response = self._request(
                    method,
                    f"{FEISHU_API}{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=_json_safe(json),
                    params=params if method == "GET" else None,
                    timeout=self._timeout,
                )
                payload = response.json()
            except Exception as error:
                raise FeishuWriteError("Feishu transport failed") from error
            self._require_success(payload)
            data = payload.get("data") or {}
            page_items = data.get("items")
            if not isinstance(page_items, list):
                return _normalize_single(data)
            items.extend(page_items)
            if not data.get("has_more"):
                return _normalize_items(path, items)
            page_token = str(data.get("page_token") or "")
            if not page_token:
                raise FeishuWriteError("Feishu pagination token missing")

    def _tenant_token(self) -> str:
        if self._token and self._now() < self._token_expires_at:
            return self._token
        try:
            response = self._request(
                "POST",
                f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self._app_id,
                    "app_secret": self._app_secret.get_secret_value(),
                },
                timeout=self._timeout,
            )
            payload = response.json()
        except Exception as error:
            raise FeishuWriteError("Feishu authentication failed") from error
        self._require_success(payload)
        token = str(payload.get("tenant_access_token") or "")
        if not token:
            raise FeishuWriteError("Feishu authentication response missing token")
        expires_in = max(61, int(payload.get("expire") or 7200))
        self._token = token
        self._token_expires_at = self._now() + expires_in - 60
        return token

    @staticmethod
    def _require_success(payload: object) -> None:
        if not isinstance(payload, dict) or payload.get("code") != 0:
            code = payload.get("code", "invalid_response") if isinstance(payload, dict) else "invalid_response"
            raise FeishuWriteError(f"Feishu request failed ({code})")


def _normalize_single(data: dict) -> dict:
    record = data.get("record")
    if isinstance(record, dict):
        return {
            "record_id": record.get("record_id"),
            "fields": dict(record.get("fields") or {}),
        }
    return dict(data)


def _normalize_items(path: str, items: list[dict]) -> dict:
    if path.endswith("/tables"):
        return {
            "tables": [
                {"id": item.get("table_id", item.get("id")), "name": item.get("name")}
                for item in items
            ]
        }
    if path.endswith("/fields"):
        normalized = []
        for item in items:
            field = {
                key: value
                for key, value in item.items()
                if key not in {"field_id", "field_name"}
            }
            field["id"] = item.get("field_id", item.get("id"))
            field["name"] = item.get("field_name", item.get("name"))
            normalized.append(field)
        return {"fields": normalized}
    return {
        "records": [
            {"record_id": item.get("record_id"), "fields": dict(item.get("fields") or {})}
            for item in items
        ]
    }


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
