from enum import Enum

from .errors import ValidationError


class TaskStatus(str, Enum):
    INBOX = "INBOX"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


_TRANSITIONS = {
    (TaskStatus.INBOX, "plan"): TaskStatus.PLANNED,
    (TaskStatus.PLANNED, "start"): TaskStatus.IN_PROGRESS,
    (TaskStatus.PLANNED, "wait"): TaskStatus.WAITING,
    (TaskStatus.IN_PROGRESS, "wait"): TaskStatus.WAITING,
    (TaskStatus.WAITING, "start"): TaskStatus.IN_PROGRESS,
    (TaskStatus.INBOX, "complete"): TaskStatus.DONE,
    (TaskStatus.PLANNED, "complete"): TaskStatus.DONE,
    (TaskStatus.IN_PROGRESS, "complete"): TaskStatus.DONE,
    (TaskStatus.WAITING, "complete"): TaskStatus.DONE,
    (TaskStatus.INBOX, "skip"): TaskStatus.SKIPPED,
    (TaskStatus.PLANNED, "skip"): TaskStatus.SKIPPED,
    (TaskStatus.IN_PROGRESS, "skip"): TaskStatus.SKIPPED,
    (TaskStatus.WAITING, "skip"): TaskStatus.SKIPPED,
    (TaskStatus.INBOX, "cancel"): TaskStatus.CANCELLED,
    (TaskStatus.PLANNED, "cancel"): TaskStatus.CANCELLED,
    (TaskStatus.IN_PROGRESS, "cancel"): TaskStatus.CANCELLED,
    (TaskStatus.WAITING, "cancel"): TaskStatus.CANCELLED,
}


def transition_task(current: TaskStatus, event: str) -> TaskStatus:
    try:
        return _TRANSITIONS[(current, event)]
    except KeyError as error:
        raise ValidationError(f"invalid task transition: {current.value}/{event}") from error
