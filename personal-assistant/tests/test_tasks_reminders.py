from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from personal_assistant.errors import ValidationError
from personal_assistant.reminders import ReminderStatus, resolve_schedule, transition_reminder
from personal_assistant.router import TaskReminderRequest
from personal_assistant.tasks import TaskStatus, transition_task


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 22, 18, 0, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    ("daypart", "hour", "minute"),
    [
        ("morning", 8, 30),
        ("noon", 12, 0),
        ("afternoon", 15, 0),
        ("evening", 19, 0),
        ("tonight", 20, 0),
    ],
)
def test_dayparts(daypart, hour, minute):
    request = TaskReminderRequest(
        operation_index=0,
        title="洗衣服",
        date_reference="tomorrow" if daypart != "tonight" else "today",
        daypart=daypart,
    )
    scheduled = resolve_schedule(request, NOW)
    assert (scheduled.hour, scheduled.minute) == (hour, minute)


def test_tomorrow_evening_is_next_day_1900():
    request = TaskReminderRequest(
        operation_index=0,
        title="洗衣服",
        date_reference="tomorrow",
        daypart="evening",
    )
    assert resolve_schedule(request, NOW) == datetime(2026, 9, 23, 19, 0, tzinfo=SHANGHAI)


def test_derived_quiet_hour_moves_to_0800():
    request = TaskReminderRequest(
        operation_index=0,
        title="喝水",
        date_reference="tomorrow",
        daypart=None,
        default_time="02:00",
    )
    assert resolve_schedule(request, NOW).hour == 8


def test_explicit_quiet_hour_is_honored():
    request = TaskReminderRequest(
        operation_index=0,
        title="接人",
        date_reference="tomorrow",
        explicit_time="02:00",
    )
    assert resolve_schedule(request, NOW).hour == 2


def test_naive_received_time_is_rejected():
    request = TaskReminderRequest(
        operation_index=0, title="洗衣服", date_reference="tomorrow", daypart="evening"
    )
    with pytest.raises(ValidationError, match="timezone-aware"):
        resolve_schedule(request, datetime(2026, 9, 22, 18, 0))


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (TaskStatus.INBOX, "plan", TaskStatus.PLANNED),
        (TaskStatus.PLANNED, "start", TaskStatus.IN_PROGRESS),
        (TaskStatus.PLANNED, "complete", TaskStatus.DONE),
        (TaskStatus.PLANNED, "skip", TaskStatus.SKIPPED),
        (TaskStatus.WAITING, "cancel", TaskStatus.CANCELLED),
    ],
)
def test_task_state_transitions(current, event, expected):
    assert transition_task(current, event) is expected


@pytest.mark.parametrize(
    ("current", "event", "followups", "max_followups", "expected"),
    [
        (ReminderStatus.SCHEDULED, "sent", 0, 1, ReminderStatus.AWAITING_CONFIRMATION),
        (ReminderStatus.AWAITING_CONFIRMATION, "complete", 0, 1, ReminderStatus.COMPLETED),
        (ReminderStatus.AWAITING_CONFIRMATION, "snooze", 0, 1, ReminderStatus.SNOOZED),
        (ReminderStatus.AWAITING_CONFIRMATION, "skip", 0, 1, ReminderStatus.SKIPPED),
        (ReminderStatus.AWAITING_CONFIRMATION, "cancel", 0, 1, ReminderStatus.CANCELLED),
        (ReminderStatus.AWAITING_CONFIRMATION, "no_reply", 0, 1, ReminderStatus.SCHEDULED),
        (ReminderStatus.AWAITING_CONFIRMATION, "no_reply", 1, 1, ReminderStatus.EXHAUSTED),
    ],
)
def test_reminder_state_transitions(current, event, followups, max_followups, expected):
    assert transition_reminder(current, event, followups, max_followups) is expected


@pytest.mark.parametrize(
    "terminal",
    [
        ReminderStatus.COMPLETED,
        ReminderStatus.SKIPPED,
        ReminderStatus.CANCELLED,
        ReminderStatus.EXHAUSTED,
    ],
)
def test_terminal_reminder_cannot_transition(terminal):
    with pytest.raises(ValidationError):
        transition_reminder(terminal, "sent", 0, 1)
