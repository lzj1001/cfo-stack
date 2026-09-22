from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable

from .errors import FeishuWriteError, NotFoundError


@dataclass(frozen=True)
class VerifiedRecord:
    record_id: str
    fields: dict[str, object]
    verified: bool = True


@dataclass(frozen=True)
class TableSchema:
    table_id: str
    name: str


@dataclass(frozen=True)
class FieldSchema:
    field_id: str
    name: str
    field_type: str
    link_table: str | None = None
    bidirectional: bool = False
    bidirectional_link_field_id: str | None = None


class FeishuAdapter:
    def __init__(self, request: Callable[..., dict]) -> None:
        self._request = request

    def get_record(self, base_token: str, table_id: str, record_id: str) -> dict:
        response = self._request(
            "GET", self._record_path(base_token, table_id, record_id), json=None
        )
        fields = response.get("fields") if isinstance(response, dict) else None
        if not isinstance(fields, dict):
            raise NotFoundError(record_id)
        return {"record_id": response.get("record_id", record_id), "fields": fields}

    def list_tables(self, base_token: str) -> list[TableSchema]:
        response = self._request(
            "GET", f"/bitable/v1/apps/{base_token}/tables", json=None
        )
        items = response.get("tables") if isinstance(response, dict) else None
        if not isinstance(items, list):
            raise NotFoundError(base_token)
        return [
            TableSchema(table_id=str(item["id"]), name=str(item["name"]))
            for item in items
        ]

    def list_fields(self, base_token: str, table_id: str) -> list[FieldSchema]:
        response = self._request(
            "GET", f"/bitable/v1/apps/{base_token}/tables/{table_id}/fields", json=None
        )
        items = response.get("fields") if isinstance(response, dict) else None
        if not isinstance(items, list):
            raise NotFoundError(table_id)
        return [
            FieldSchema(
                field_id=str(item["id"]),
                name=str(item["name"]),
                field_type=str(item["type"]),
                link_table=_optional_text(item.get("link_table")),
                bidirectional=bool(item.get("bidirectional", False)),
                bidirectional_link_field_id=_optional_text(
                    item.get("bidirectional_link_field_id")
                ),
            )
            for item in items
        ]

    def list_records(self, base_token: str, table_id: str) -> list[dict]:
        response = self._request(
            "GET", self._records_path(base_token, table_id), json=None
        )
        records = response.get("records") if isinstance(response, dict) else None
        if records is None and isinstance(response, dict) and response.get("total") == 0:
            return []
        if not isinstance(records, list):
            raise NotFoundError(table_id)
        return [
            {
                "record_id": str(record["record_id"]),
                "fields": dict(record.get("fields") or {}),
            }
            for record in records
        ]

    def create_verified(
        self,
        base_token: str,
        table_id: str,
        fields: dict[str, object],
        *,
        verify_fields: tuple[str, ...],
    ) -> VerifiedRecord:
        path = self._records_path(base_token, table_id)
        try:
            response = self._request("POST", path, json={"fields": fields})
        except Exception as error:
            raise FeishuWriteError("create failed") from error
        record_id = str(response.get("record_id") or "")
        if not record_id:
            raise FeishuWriteError("create response omitted record_id")
        try:
            return self._verify(base_token, table_id, record_id, fields, verify_fields)
        except FeishuWriteError as error:
            raise FeishuWriteError(str(error), record_id=record_id) from error

    def verify_existing(
        self,
        base_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, object],
        *,
        verify_fields: tuple[str, ...],
    ) -> VerifiedRecord:
        return self._verify(base_token, table_id, record_id, fields, verify_fields)

    def update_verified(
        self,
        base_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, object],
        *,
        verify_fields: tuple[str, ...],
    ) -> VerifiedRecord:
        try:
            self._request(
                "PUT",
                self._record_path(base_token, table_id, record_id),
                json={"fields": fields},
            )
        except Exception as error:
            raise FeishuWriteError("update failed") from error
        return self._verify(base_token, table_id, record_id, fields, verify_fields)

    def _verify(
        self,
        base_token: str,
        table_id: str,
        record_id: str,
        expected: dict[str, object],
        verify_fields: tuple[str, ...],
    ) -> VerifiedRecord:
        try:
            record = self.get_record(base_token, table_id, record_id)
        except NotFoundError as error:
            raise FeishuWriteError("readback not found") from error
        except Exception as error:
            raise FeishuWriteError("readback failed") from error
        actual = record["fields"]
        for name in verify_fields:
            if name not in expected or name not in actual or not _same_value(
                actual[name], expected[name]
            ):
                raise FeishuWriteError(f"readback mismatch: {name}")
        return VerifiedRecord(record_id=record_id, fields=actual)

    @staticmethod
    def _records_path(base_token: str, table_id: str) -> str:
        return f"/bitable/v1/apps/{base_token}/tables/{table_id}/records"

    @classmethod
    def _record_path(cls, base_token: str, table_id: str, record_id: str) -> str:
        return f"{cls._records_path(base_token, table_id)}/{record_id}"


def _same_value(actual: object, expected: object) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except (InvalidOperation, ValueError):
            return False
    return actual == expected


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)
