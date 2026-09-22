from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import uuid4

from .errors import DuplicateOperation, FeishuWriteError, NotFoundError
from .models import Receipt, operation_key
from .state import OperationRow, StateStore


class ReceiptService:
    def __init__(
        self,
        *,
        store: StateStore,
        feishu: object,
        audit_sink: Callable[[Receipt], None],
        base_token: str,
        table_id: str,
        verify_fields: tuple[str, ...],
    ) -> None:
        self._store = store
        self._feishu = feishu
        self._audit_sink = audit_sink
        self._base_token = base_token
        self._table_id = table_id
        self._verify_fields = verify_fields

    def execute_create(
        self,
        message_id: str,
        operation_index: int,
        *,
        fields: dict[str, object] | Callable[[str], dict[str, object]],
    ) -> Receipt:
        key = operation_key(message_id, operation_index)
        try:
            existing = self._store.get_operation(key)
        except NotFoundError:
            existing = None

        if existing is not None:
            if existing.status in {"business_succeeded", "completed"}:
                return self.flush_pending_audit(key)
            if existing.status == "failed" and existing.target_id and existing.receipt_id:
                write_fields = fields(existing.receipt_id) if callable(fields) else fields
                try:
                    verified = self._feishu.verify_existing(
                        self._base_token,
                        self._table_id,
                        existing.target_id,
                        write_fields,
                        verify_fields=self._verify_fields,
                    )
                except FeishuWriteError:
                    return _receipt_from_row(existing, status="failed")
                recovered = self._store.recover_business_succeeded(
                    key,
                    target_id=verified.record_id,
                    receipt_id=existing.receipt_id,
                )
                return self._attempt_audit(recovered)
            return _receipt_from_row(existing, status="failed", error_code="DuplicateOperation")

        try:
            self._store.claim(key, message_id, operation_index)
        except DuplicateOperation:
            return self.execute_create(message_id, operation_index, fields=fields)

        receipt_id = f"rcpt_{uuid4().hex}"
        write_fields = fields(receipt_id) if callable(fields) else fields
        try:
            verified = self._feishu.create_verified(
                self._base_token,
                self._table_id,
                write_fields,
                verify_fields=self._verify_fields,
            )
        except FeishuWriteError as error:
            failed = self._store.mark_failed(
                key,
                error_code=error.__class__.__name__,
                max_retries=1,
                target_id=error.record_id,
                receipt_id=receipt_id,
            )
            return _receipt_from_row(
                failed,
                status="failed",
                receipt_id=receipt_id,
                error_code=error.__class__.__name__,
            )

        row = self._store.mark_business_succeeded(
            key, target_id=verified.record_id, receipt_id=receipt_id
        )
        return self._attempt_audit(row)

    def execute_update(
        self,
        message_id: str,
        operation_index: int,
        *,
        record_id: str,
        fields: dict[str, object] | Callable[[str], dict[str, object]],
    ) -> Receipt:
        key = operation_key(message_id, operation_index)
        try:
            existing = self._store.get_operation(key)
        except NotFoundError:
            existing = None
        if existing is not None:
            if existing.status in {"business_succeeded", "completed"}:
                return self.flush_pending_audit(key)
            return _receipt_from_row(existing, status="failed", error_code="DuplicateOperation")

        self._store.claim(key, message_id, operation_index)
        receipt_id = f"rcpt_{uuid4().hex}"
        write_fields = fields(receipt_id) if callable(fields) else fields
        try:
            verified = self._feishu.update_verified(
                self._base_token,
                self._table_id,
                record_id,
                write_fields,
                verify_fields=self._verify_fields,
            )
        except FeishuWriteError as error:
            failed = self._store.mark_failed(
                key, error_code=error.__class__.__name__, max_retries=1
            )
            return _receipt_from_row(
                failed,
                status="failed",
                receipt_id=receipt_id,
                error_code=error.__class__.__name__,
            )
        row = self._store.mark_business_succeeded(
            key, target_id=verified.record_id, receipt_id=receipt_id
        )
        return self._attempt_audit(row)

    def flush_pending_audit(self, key: str) -> Receipt:
        row = self._store.get_operation(key)
        if row.status == "completed":
            return _receipt_from_row(row, status="completed")
        if row.status != "business_succeeded":
            return _receipt_from_row(row, status="failed")
        return self._attempt_audit(row)

    def _attempt_audit(self, row: OperationRow) -> Receipt:
        pending = _receipt_from_row(row, status="completed")
        try:
            self._audit_sink(pending)
        except Exception:
            return pending
        completed = self._store.mark_audit_completed(row.operation_key)
        return _receipt_from_row(completed, status="completed")


def _receipt_from_row(
    row: OperationRow,
    *,
    status: str,
    receipt_id: str | None = None,
    error_code: str | None = None,
) -> Receipt:
    return Receipt(
        receipt_id=receipt_id or row.receipt_id or f"failed_{row.operation_key}",
        operation_key=row.operation_key,
        message_id=row.message_id,
        operation_index=row.operation_index,
        status=status,
        target_id=row.target_id,
        error_code=error_code or row.error_code,
        audit_status=row.audit_status,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
    )
