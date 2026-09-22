from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Intent, MessageContext


TransactionType = Literal[
    "EXPENSE",
    "INCOME",
    "REFUND",
    "TRANSFER",
    "CREDIT_CARD_REPAYMENT",
    "INVESTMENT",
    "BORROW",
    "LEND",
    "OTHER",
]


class AccountingItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_index: int = Field(ge=0)
    description: str = Field(min_length=1)
    amount: str = Field(min_length=1)
    currency: str = "CNY"
    transaction_type: TransactionType
    merchant: str | None = None
    category: str | None = None
    subcategory: str | None = None
    payment_account: str | None = None
    related_transaction_id: str | None = None


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_index: int = Field(ge=0)
    match_text: str = Field(min_length=1)
    old_amount: str | None = None
    new_amount: str = Field(min_length=1)


class TaskReminderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_index: int = Field(ge=0)
    title: str = Field(min_length=1)
    details: str = ""
    date_reference: Literal["today", "tomorrow", "date"] = "today"
    date: str | None = None
    daypart: Literal["morning", "noon", "afternoon", "evening", "tonight"] | None = None
    explicit_time: str | None = None
    default_time: str | None = None
    priority: Literal["low", "medium", "high"] = "medium"
    project: str | None = None


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_index: int = Field(ge=0)
    title: str = Field(min_length=1)
    capture_type: Literal["idea", "thought", "insight", "note", "journal"]
    tags: list[str] = Field(max_length=5)
    related_topics: list[str]


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["router-v1"]
    intent: Intent
    confidence: str
    items: list[AccountingItem]
    correction: CorrectionRequest | None
    task_reminder: TaskReminderRequest | None = None
    capture: CaptureRequest | None = None
    ambiguities: list[str]
    reason_code: str

    @field_validator("intent", mode="before")
    @classmethod
    def parse_intent(cls, value: object) -> Intent:
        return value if isinstance(value, Intent) else Intent(str(value))

    @field_validator("reason_code")
    @classmethod
    def require_reason_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason_code must not be blank")
        return value.strip()

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: str) -> str:
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("confidence must be a decimal probability") from error
        if not parsed.is_finite() or not Decimal("0") <= parsed <= Decimal("1"):
            raise ValueError("confidence must be between zero and one")
        return value

    @model_validator(mode="after")
    def validate_accounting_shape(self) -> "RouterDecision":
        indexes = [item.operation_index for item in self.items]
        if indexes != list(range(len(indexes))):
            raise ValueError("operation indexes must be unique and contiguous from zero")
        if self.items and self.correction is not None:
            raise ValueError("items and correction cannot coexist")
        return self


class Router:
    def __init__(self, llm: object, *, model: str) -> None:
        self._llm = llm
        self._model = model

    async def route(self, ctx: MessageContext) -> RouterDecision:
        explicit_capture = re.match(
            r"^\s*(?:记录一个想法|记录想法|闪念)\s*[：:]\s*(.+?)\s*$",
            ctx.text,
            re.DOTALL,
        )
        if explicit_capture:
            title = explicit_capture.group(1)
            return RouterDecision(
                schema_version="router-v1",
                intent=Intent.CAPTURE,
                confidence="1.00",
                items=[],
                correction=None,
                capture=CaptureRequest(
                    operation_index=0,
                    title=title,
                    capture_type="idea",
                    tags=[],
                    related_topics=[],
                ),
                ambiguities=[],
                reason_code="explicit_capture",
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "Router schema router-v1. Return JSON only with intent, confidence as a decimal "
                    "string, accounting items or one correction, optional task_reminder or capture, "
                    "ambiguities, and reason_code. For TASK or REMINDER, fill task_reminder "
                    "with title, relative date reference, daypart or explicit time. "
                    "Do not output hidden reasoning. Preserve each amount as text."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"timezone={ctx.timezone}\nreceived_at={ctx.received_at.isoformat()}\n"
                    f"text={ctx.text}"
                ),
            },
        ]
        return await self._llm.structured(messages, RouterDecision, model=self._model)


@dataclass(frozen=True)
class GateDecision:
    execute: bool
    question: str | None = None


class ExecutionGate:
    _SAFE_REASON_CODES = {
        "explicit_expense",
        "explicit_income",
        "explicit_refund",
        "explicit_transfer",
        "credit_card_repayment",
        "investment",
        "correction",
    }
    _PURPOSE_WORDS = ("买", "消费", "借给", "借款", "还款", "退款", "转账", "礼金")

    @classmethod
    def evaluate(cls, decision: RouterDecision, raw_text: str) -> GateDecision:
        if decision.intent is Intent.UNKNOWN:
            return GateDecision(
                False, "你想让我记账、创建任务、设置提醒，还是保存一条想法？"
            )

        recipient_amount = re.search(r"给[^\d，。,.\s]+\s*(\d+(?:\.\d{1,2})?)", raw_text)
        if recipient_amount and not any(word in raw_text for word in cls._PURPOSE_WORDS):
            return GateDecision(
                False,
                f"这 {recipient_amount.group(1)} 元是消费、借给对方、还款，还是其他用途？",
            )

        if decision.ambiguities:
            question = decision.ambiguities[0].rstrip("？?") + "？"
            return GateDecision(False, question)

        try:
            confidence = Decimal(decision.confidence)
        except InvalidOperation:
            return GateDecision(False, "这条信息不够明确，你希望我具体做什么？")

        if confidence < Decimal("0.65"):
            return GateDecision(False, "这条信息不够明确，你希望我具体做什么？")

        has_accounting_payload = bool(decision.items or decision.correction)
        if decision.intent is Intent.ACCOUNTING and not has_accounting_payload:
            return GateDecision(False, "这笔记录缺少金额或用途，可以补充一下吗？")

        if confidence < Decimal("0.90") and decision.reason_code not in cls._SAFE_REASON_CODES:
            return GateDecision(False, "这条信息还不够确定，可以补充一下吗？")

        return GateDecision(True)
