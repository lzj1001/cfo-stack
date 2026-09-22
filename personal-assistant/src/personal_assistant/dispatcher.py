from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from .errors import ValidationError
from .reminders import ReminderStatus, transition_reminder
from .state import StateStore


class ReminderDispatcher:
    def __init__(
        self,
        *,
        store: StateStore,
        send_dm: Callable[[str, str], bool],
        task_title: Callable[[str], str],
        update_remote: Callable[[str, dict[str, object]], None],
    ) -> None:
        self._store = store
        self._send_dm = send_dm
        self._task_title = task_title
        self._update_remote = update_remote

    def run_due(self, now: datetime) -> int:
        _require_aware(now)
        now_utc = now.astimezone(timezone.utc)
        lease_until = now_utc + timedelta(minutes=5)
        jobs = self._store.claim_due_reminders(
            now_utc.isoformat(), lease_until=lease_until.isoformat()
        )
        sent = 0
        for job in jobs:
            title = self._task_title(job.task_id)
            text = f"提醒：{title}。完成后回复“完成了”，也可以回复“晚点提醒”或“今天不做”。"
            if not self._send_dm(job.user_id, text):
                self._store.update_reminder_job(
                    job.reminder_id,
                    status=job.status,
                    next_action_at=(now_utc + timedelta(minutes=5)).isoformat(),
                    follow_up_count=job.follow_up_count,
                    last_sent_at=job.last_sent_at,
                )
                continue
            status = transition_reminder(
                ReminderStatus(job.status),
                "sent",
                job.follow_up_count,
                job.max_follow_ups,
            )
            fields = {
                "status": status.value,
                "last_sent_at": int(now_utc.timestamp() * 1000),
                "next_action_at": int((now_utc + timedelta(minutes=30)).timestamp() * 1000),
                "follow_up_count": job.follow_up_count,
            }
            self._store.update_reminder_job(
                job.reminder_id,
                status=status.value,
                next_action_at=(now_utc + timedelta(minutes=30)).isoformat(),
                follow_up_count=job.follow_up_count,
                last_sent_at=now_utc.isoformat(),
            )
            self._update_remote(job.reminder_id, fields)
            sent += 1
        return sent

    def handle_no_reply(self, reminder_id: str, now: datetime) -> ReminderStatus:
        _require_aware(now)
        now_utc = now.astimezone(timezone.utc)
        job = self._store.get_reminder_job(reminder_id)
        status = transition_reminder(
            ReminderStatus(job.status),
            "no_reply",
            job.follow_up_count,
            job.max_follow_ups,
        )
        next_count = job.follow_up_count + 1 if status is ReminderStatus.SCHEDULED else job.follow_up_count
        next_action = (
            now_utc + timedelta(minutes=30)
            if status is ReminderStatus.SCHEDULED
            else now_utc
        )
        fields = {
            "status": status.value,
            "follow_up_count": next_count,
            "next_action_at": int(next_action.timestamp() * 1000),
        }
        self._store.update_reminder_job(
            reminder_id,
            status=status.value,
            next_action_at=next_action.isoformat(),
            follow_up_count=next_count,
            last_sent_at=job.last_sent_at,
        )
        self._update_remote(reminder_id, fields)
        return status


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("dispatcher time must be timezone-aware")
