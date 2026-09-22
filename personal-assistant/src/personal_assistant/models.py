from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator


class Intent(str, Enum):
    ACCOUNTING = "ACCOUNTING"
    TASK = "TASK"
    REMINDER = "REMINDER"
    CAPTURE = "CAPTURE"
    REVIEW = "REVIEW"
    CHAT = "CHAT"
    UNKNOWN = "UNKNOWN"


class OperationStatus(str, Enum):
    CLAIMED = "claimed"
    BUSINESS_SUCCEEDED = "business_succeeded"
    FAILED = "failed"
    COMPLETED = "completed"


def operation_key(message_id: str, operation_index: int) -> str:
    if not message_id.strip() or operation_index < 0:
        raise ValueError("invalid operation identity")
    return f"{message_id.strip()}:{operation_index}"


class MessageContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    message_id: str
    user_id: str
    text: str
    received_at: datetime
    timezone: str = "Asia/Shanghai"

    @field_validator("message_id", "user_id", "text")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("received_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value

    @field_validator("timezone")
    @classmethod
    def require_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone is invalid") from error
        return value


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str
    status: Literal["completed", "needs_clarification", "failed", "chat"]
    receipt_id: str | None = None


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    receipt_id: str
    operation_key: str
    message_id: str
    operation_index: int
    status: Literal["completed", "failed", "duplicate"]
    target_id: str | None = None
    error_code: str | None = None
    audit_status: Literal["not_required", "pending", "completed"] = "not_required"
    created_at: datetime
    updated_at: datetime
