from decimal import Decimal

from pydantic import SecretStr
import pytest

from personal_assistant.errors import FeishuWriteError
from personal_assistant.feishu_runtime import FeishuOpenAPITransport


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_transport_authenticates_normalizes_tables_and_reuses_token():
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return Response({"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
        return Response(
            {
                "code": 0,
                "data": {
                    "items": [{"table_id": "tbl_daily", "name": "日常财务记录"}],
                    "has_more": False,
                },
            }
        )

    transport = FeishuOpenAPITransport(
        app_id="app-id",
        app_secret=SecretStr("app-secret"),
        request=request,
        now=lambda: 1000.0,
    )
    first = transport("GET", "/bitable/v1/apps/base/tables")
    second = transport("GET", "/bitable/v1/apps/base/tables")

    assert first == second == {"tables": [{"id": "tbl_daily", "name": "日常财务记录"}]}
    assert sum("tenant_access_token" in url for _, url, _ in calls) == 1
    business = [call for call in calls if "/bitable/" in call[1]]
    assert business[0][2]["headers"]["Authorization"] == "Bearer tenant-token"


def test_transport_paginates_fields():
    pages = iter(
        [
            {"code": 0, "data": {"items": [{"field_id": "f1", "field_name": "金额", "type": "number"}], "has_more": True, "page_token": "next"}},
            {"code": 0, "data": {"items": [{"field_id": "f2", "field_name": "交易ID", "type": "text"}], "has_more": False}},
        ]
    )

    def request(method, url, **kwargs):
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return Response({"code": 0, "tenant_access_token": "token", "expire": 7200})
        return Response(next(pages))

    transport = FeishuOpenAPITransport(
        app_id="id", app_secret=SecretStr("secret"), request=request, now=lambda: 1.0
    )
    assert transport("GET", "/bitable/v1/apps/base/tables/daily/fields") == {
        "fields": [
            {"id": "f1", "name": "金额", "type": "number"},
            {"id": "f2", "name": "交易ID", "type": "text"},
        ]
    }


def test_transport_error_does_not_leak_credentials():
    def request(method, url, **kwargs):
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return Response({"code": 0, "tenant_access_token": "tenant-secret", "expire": 7200})
        return Response({"code": 1254001, "msg": "bad request"})

    transport = FeishuOpenAPITransport(
        app_id="id",
        app_secret=SecretStr("app-super-secret"),
        request=request,
        now=lambda: 1.0,
    )
    with pytest.raises(FeishuWriteError) as caught:
        transport("POST", "/bitable/v1/apps/base/tables/daily/records", json={})
    message = str(caught.value)
    assert "1254001" in message
    assert "app-super-secret" not in message
    assert "tenant-secret" not in message


def test_transport_serializes_decimal_as_json_number():
    captured = {}

    def request(method, url, **kwargs):
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return Response({"code": 0, "tenant_access_token": "token", "expire": 7200})
        captured.update(kwargs["json"])
        return Response({"code": 0, "data": {"record": {"record_id": "rec_1", "fields": {}}}})

    transport = FeishuOpenAPITransport(
        app_id="id", app_secret=SecretStr("secret"), request=request, now=lambda: 1.0
    )
    transport(
        "POST",
        "/bitable/v1/apps/base/tables/daily/records",
        json={"fields": {"金额": Decimal("35.50"), "来源分项序号": Decimal("0")}},
    )
    assert captured == {"fields": {"金额": 35.5, "来源分项序号": 0}}
    assert type(captured["fields"]["金额"]) is float
    assert type(captured["fields"]["来源分项序号"]) is int
