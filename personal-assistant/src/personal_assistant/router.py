from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import timedelta
import re
from typing import Literal
from zoneinfo import ZoneInfo

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
        deterministic_reminder = _parse_explicit_lead_reminder(ctx)
        if deterministic_reminder is not None:
            return deterministic_reminder
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


def _parse_explicit_lead_reminder(ctx: MessageContext) -> RouterDecision | None:
    if "提醒" not in ctx.text or "提前" not in ctx.text:
        return None
    range_match = re.search(
        r"(?P<start>\d{1,2})\s*点\s*到\s*(?P<end>\d{1,2})\s*点", ctx.text
    )
    time_match = range_match or re.search(r"(?P<start>\d{1,2})\s*点", ctx.text)
    lead_match = re.search(
        r"提前\s*(?P<value>半个钟|半小时|\d+(?:\.\d+)?)\s*(?P<unit>分钟|分|小时|钟)?",
        ctx.text,
    )
    title_match = re.search(r"(?:要去|去|参加)([^，,。！？]+)", ctx.text)
    if not time_match or not lead_match or not title_match:
        return None

    start_hour = int(time_match.group("start"))
    end_hour = time_match.group("end")
    if start_hour > 23 or (end_hour is not None and int(end_hour) > 23):
        return None
    value = lead_match.group("value")
    if value in {"半个钟", "半小时"}:
        lead_minutes = 30
    elif lead_match.group("unit") in {"小时", "钟"}:
        lead_minutes = int(float(value) * 60)
    else:
        lead_minutes = int(float(value))
    if lead_minutes <= 0:
        return None

    try:
        zone = ZoneInfo(ctx.timezone)
    except Exception:
        return None
    local = ctx.received_at.astimezone(zone)
    event_at = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    reminder_at = event_at - timedelta(minutes=lead_minutes)
    if reminder_at < local:
        return None
    date_reference = "today"
    if reminder_at.date() == local.date() + timedelta(days=1):
        date_reference = "tomorrow"
    elif reminder_at.date() != local.date():
        return None

    title = title_match.group(1).strip()
    details = f"活动时间 {start_hour:02d}:00"
    if end_hour is not None:
        details += f"–{int(end_hour):02d}:00"
    details += f"；提前 {lead_minutes} 分钟提醒"
    return RouterDecision(
        schema_version="router-v1",
        intent=Intent.REMINDER,
        confidence="1.00",
        items=[],
        correction=None,
        task_reminder=TaskReminderRequest(
            operation_index=0,
            title=title,
            details=details,
            date_reference=date_reference,
            explicit_time=reminder_at.strftime("%H:%M"),
            priority="medium",
        ),
        ambiguities=[],
        reason_code="explicit_lead_time_reminder",
    )


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
