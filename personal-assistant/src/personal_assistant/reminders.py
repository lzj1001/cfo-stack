from __future__ import annotations

from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ValidationError
from .router import TaskReminderRequest


class ReminderStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    SNOOZED = "SNOOZED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    EXHAUSTED = "EXHAUSTED"


DAYPARTS = {
    "morning": time(8, 30),
    "noon": time(12, 0),
    "afternoon": time(15, 0),
    "evening": time(19, 0),
    "tonight": time(20, 0),
}


def resolve_schedule(
    request: TaskReminderRequest,
    received_at: datetime,
    *,
    timezone: str = "Asia/Shanghai",
) -> datetime:
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValidationError("received_at must be timezone-aware")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ValidationError("timezone is invalid") from error
    local = received_at.astimezone(zone)
    if request.date_reference == "tomorrow":
        target_date = local.date() + timedelta(days=1)
    elif request.date_reference == "date":
        if not request.date:
            raise ValidationError("explicit date is required")
        try:
            target_date = date.fromisoformat(request.date)
        except ValueError as error:
            raise ValidationError("date is invalid") from error
    else:
        target_date = local.date()

    explicit = request.explicit_time is not None
    if request.explicit_time:
        target_time = _parse_time(request.explicit_time)
    elif request.daypart:
        target_time = DAYPARTS[request.daypart]
    elif request.default_time:
        target_time = _parse_time(request.default_time)
    else:
        target_time = DAYPARTS["morning"]

    if not explicit and target_time < time(8, 0):
        target_time = time(8, 0)
    return datetime.combine(target_date, target_time, tzinfo=zone)


def transition_reminder(
    current: ReminderStatus,
    event: str,
    follow_up_count: int,
    max_follow_ups: int,
) -> ReminderStatus:
    if current in {
        ReminderStatus.COMPLETED,
        ReminderStatus.SKIPPED,
        ReminderStatus.CANCELLED,
        ReminderStatus.EXHAUSTED,
    }:
        raise ValidationError(f"terminal reminder cannot transition: {current.value}")
    if event == "sent" and current in {ReminderStatus.SCHEDULED, ReminderStatus.SNOOZED}:
        return ReminderStatus.AWAITING_CONFIRMATION
    if event == "complete":
        return ReminderStatus.COMPLETED
    if event == "snooze":
        return ReminderStatus.SNOOZED
    if event == "skip":
        return ReminderStatus.SKIPPED
    if event == "cancel":
        return ReminderStatus.CANCELLED
    if event == "no_reply" and current in {
        ReminderStatus.SENT,
        ReminderStatus.AWAITING_CONFIRMATION,
    }:
        return (
            ReminderStatus.SCHEDULED
            if follow_up_count < max_follow_ups
            else ReminderStatus.EXHAUSTED
        )
    raise ValidationError(f"invalid reminder transition: {current.value}/{event}")


def _parse_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise ValidationError("time is invalid") from error
    return parsed.replace(second=0, microsecond=0)
