from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Callable

from .accounting import format_accounting_receipt, normalize_item, to_daily_fields
from .audit import ReceiptService
from .errors import FeishuWriteError, NotFoundError, ValidationError
from .models import AssistantResponse, MessageContext, Receipt
from .router import CorrectionRequest, RouterDecision
from .state import StateStore


class AccountingService:
    def __init__(
        self,
        *,
        store: StateStore,
        daily: object,
        base_token: str,
        daily_table_id: str,
        model_name: str,
        audit_sink_factory: Callable[..., Callable[[Receipt], None]] | None = None,
    ) -> None:
        self._store = store
        self._daily = daily
        self._base_token = base_token
        self._daily_table_id = daily_table_id
        self._model_name = model_name
        if audit_sink_factory is None:
            raise ValidationError("audit sink factory is required")
        self._audit_sink_factory = audit_sink_factory

    def record(self, ctx: MessageContext, decision: RouterDecision) -> AssistantResponse:
        transactions = [
            normalize_item(
                ctx.message_id,
                item,
                raw_text=ctx.text,
                confidence=decision.confidence,
            )
            for item in decision.items
        ]
        receipts: list[Receipt] = []
        failures: list[str] = []
        for transaction in transactions:
            sink = self._audit_sink(
                ctx, decision, transaction.operation_index, action="create"
            )
            service = ReceiptService(
                store=self._store,
                feishu=self._daily,
                audit_sink=sink,
                base_token=self._base_token,
                table_id=self._daily_table_id,
                verify_fields=(
                    "交易ID",
                    "来源消息ID",
                    "来源分项序号",
                    "金额",
                    "回执ID",
                ),
            )

            def fields(receipt_id: str, tx=transaction) -> dict[str, object]:
                mapped = to_daily_fields(tx, receipt_id=receipt_id)
                mapped["发生时间"] = int(ctx.received_at.timestamp() * 1000)
                return mapped

            receipt = service.execute_create(
                ctx.message_id, transaction.operation_index, fields=fields
            )
            receipts.append(receipt)
            if receipt.status != "completed":
                failures.append(transaction.description)

        if failures:
            verified_count = len(transactions) - len(failures)
            return AssistantResponse(
                text=(
                    f"已确认写入 {verified_count} 笔；未写入：{'、'.join(failures)}。"
                    "请求已保留，没有重复记账。"
                ),
                status="failed",
                receipt_id=receipts[0].receipt_id if receipts else None,
            )
        return AssistantResponse(
            text=format_accounting_receipt(transactions),
            status="completed",
            receipt_id=receipts[0].receipt_id if receipts else None,
        )

    def correct(
        self, ctx: MessageContext, correction: CorrectionRequest
    ) -> AssistantResponse:
        records = self._daily.list_records(self._base_token, self._daily_table_id)
        old_amount = _amount(correction.old_amount) if correction.old_amount else None
        candidates = []
        for record in records:
            fields = record["fields"]
            haystack = " ".join(
                str(fields.get(name) or "")
                for name in ("原始口述", "对方或商户原文", "真实用途大类", "真实用途小类")
            )
            if correction.match_text not in haystack:
                continue
            if old_amount is not None and Decimal(str(fields.get("金额") or 0)) != old_amount:
                continue
            candidates.append(record)

        if len(candidates) != 1:
            detail = "没有找到唯一对应记录" if not candidates else "找到多条可能记录"
            return AssistantResponse(
                text=f"{detail}，请补充日期或商户以便确认？",
                status="needs_clarification",
            )

        candidate = candidates[0]
        original_fields = candidate["fields"]
        original_id = str(original_fields.get("交易ID") or "")
        if not original_id:
            return AssistantResponse(
                text="原记录缺少稳定交易ID，暂时不能安全更正。",
                status="failed",
            )
        new_amount = _amount(correction.new_amount)
        sink = self._audit_sink(ctx, None, correction.operation_index, action="update")
        service = ReceiptService(
            store=self._store,
            feishu=self._daily,
            audit_sink=sink,
            base_token=self._base_token,
            table_id=self._daily_table_id,
            verify_fields=("金额", "回执ID"),
        )
        receipt = service.execute_update(
            ctx.message_id,
            correction.operation_index,
            record_id=candidate["record_id"],
            fields=lambda receipt_id: {"金额": new_amount, "回执ID": receipt_id},
        )
        if receipt.status != "completed":
            return AssistantResponse(
                text="更正暂时没有写入成功，原记录保持不变。",
                status="failed",
                receipt_id=receipt.receipt_id,
            )
        before = old_amount or Decimal(str(original_fields.get("金额")))
        return AssistantResponse(
            text=f"已更正：{correction.match_text} ¥{_display(before)} → ¥{_display(new_amount)}",
            status="completed",
            receipt_id=receipt.receipt_id,
        )

    def _audit_sink(
        self,
        ctx: MessageContext,
        decision: RouterDecision | None,
        operation_index: int,
        *,
        action: str,
    ) -> Callable[[Receipt], None]:
        return self._audit_sink_factory(
            ctx=ctx,
            decision=decision,
            operation_index=operation_index,
            action=action,
            model_name=self._model_name,
        )


class RemoteAuditSink:
    def __init__(
        self,
        *,
        adapter: object,
        base_token: str,
        audit_table_id: str,
        context: dict[str, object],
    ) -> None:
        self._adapter = adapter
        self._base_token = base_token
        self._table_id = audit_table_id
        self._context = context

    def __call__(self, receipt: Receipt) -> None:
        expected = {
            "回执ID": receipt.receipt_id,
            "消息ID": receipt.message_id,
            "来源分项序号": receipt.operation_index,
            "原始输入": self._context["raw_input"],
            "意图": self._context["intent"],
            "模型名称": self._context["model_name"],
            "模型置信度": Decimal(str(self._context["model_confidence"])),
            "操作": self._context["action"],
            "目标ID": receipt.target_id or "",
            "结果": "succeeded" if receipt.status == "completed" else "failed",
            "错误代码": receipt.error_code or "",
        }
        matches = [
            record
            for record in self._adapter.list_records(self._base_token, self._table_id)
            if str(record["fields"].get("回执ID") or "") == receipt.receipt_id
        ]
        if matches:
            if len(matches) != 1 or any(
                not _audit_equal(matches[0]["fields"].get(name), value)
                for name, value in expected.items()
            ):
                raise FeishuWriteError("conflicting Audit receipt")
            return
        self._adapter.create_verified(
            self._base_token,
            self._table_id,
            expected,
            verify_fields=("回执ID", "消息ID", "来源分项序号", "操作", "目标ID", "结果"),
        )


def _amount(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValidationError("correction amount is invalid") from error
    if not value.is_finite() or value <= 0 or value != value.quantize(Decimal("0.01")):
        raise ValidationError("correction amount is invalid")
    return value


def _display(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _audit_equal(actual: object, expected: object) -> bool:
    if actual is None and expected == "":
        return True
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except (InvalidOperation, ValueError):
            return False
    return actual == expected
