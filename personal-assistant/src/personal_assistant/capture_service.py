from __future__ import annotations

from typing import Callable

from .audit import ReceiptService
from .capture import normalize_capture, to_capture_fields
from .errors import ValidationError
from .models import AssistantResponse, MessageContext, Receipt
from .router import CaptureRequest
from .state import StateStore


class CaptureService:
    def __init__(
        self,
        *,
        store: StateStore,
        adapter: object,
        base_token: str,
        table_id: str,
        audit_sink_factory: Callable[..., Callable[[Receipt], None]] | None,
    ) -> None:
        if audit_sink_factory is None:
            raise ValidationError("audit sink factory is required")
        self._store = store
        self._adapter = adapter
        self._base_token = base_token
        self._table_id = table_id
        self._audit_sink_factory = audit_sink_factory

    def capture(
        self,
        ctx: MessageContext,
        request: CaptureRequest,
        *,
        confidence: str,
    ) -> AssistantResponse:
        normalized = normalize_capture(
            ctx.message_id, request, raw_text=ctx.text, confidence=confidence
        )
        receipts = ReceiptService(
            store=self._store,
            feishu=self._adapter,
            audit_sink=self._audit_sink_factory(ctx=ctx, kind="capture", action="create"),
            base_token=self._base_token,
            table_id=self._table_id,
            verify_fields=("capture_id", "raw_text", "receipt_id"),
        )
        receipt = receipts.execute_create(
            ctx.message_id,
            request.operation_index,
            fields=lambda receipt_id: to_capture_fields(
                normalized,
                receipt_id=receipt_id,
                captured_at_ms=int(ctx.received_at.timestamp() * 1000),
            ),
        )
        if receipt.status != "completed":
            return AssistantResponse(
                text="这条内容暂时没有保存成功，没有重复创建。",
                status="failed",
                receipt_id=receipt.receipt_id,
            )
        return AssistantResponse(
            text=f"已保存闪念：{normalized.title}",
            status="completed",
            receipt_id=receipt.receipt_id,
        )
