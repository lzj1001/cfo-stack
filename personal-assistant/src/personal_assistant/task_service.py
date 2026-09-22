from __future__ import annotations

from datetime import timedelta, timezone
from typing import Callable

from .audit import ReceiptService
from .errors import ValidationError
from .models import AssistantResponse, MessageContext, Receipt, operation_key
from .reminders import resolve_schedule
from .reminders import ReminderStatus, transition_reminder
from .router import TaskReminderRequest
from .state import StateStore
from .tasks import TaskStatus, transition_task


class TaskReminderService:
    def __init__(
        self,
        *,
        store: StateStore,
        tasks: object,
        reminders: object,
        base_token: str,
        tasks_table_id: str,
        reminders_table_id: str,
        max_follow_ups: int,
        audit_sink_factory: Callable[..., Callable[[Receipt], None]] | None,
    ) -> None:
        if audit_sink_factory is None:
            raise ValidationError("audit sink factory is required")
        self._store = store
        self._tasks = tasks
        self._reminders = reminders
        self._base_token = base_token
        self._tasks_table = tasks_table_id
        self._reminders_table = reminders_table_id
        self._max_follow_ups = max_follow_ups
        self._audit_sink_factory = audit_sink_factory

    def create(
        self, ctx: MessageContext, request: TaskReminderRequest
    ) -> AssistantResponse:
        scheduled = resolve_schedule(request, ctx.received_at, timezone=ctx.timezone)
        task_index = request.operation_index
        reminder_index = task_index + 1
        task_id = operation_key(ctx.message_id, task_index)
        reminder_id = operation_key(ctx.message_id, reminder_index)

        task_receipts = ReceiptService(
            store=self._store,
            feishu=self._tasks,
            audit_sink=self._audit_sink_factory(ctx=ctx, kind="task", action="create"),
            base_token=self._base_token,
            table_id=self._tasks_table,
            verify_fields=("task_id", "source_message_id", "source_operation_index", "receipt_id"),
        )
        task_receipt = task_receipts.execute_create(
            ctx.message_id,
            task_index,
            fields=lambda receipt_id: {
                "task_id": task_id,
                "title": request.title,
                "details": request.details,
                "status": "PLANNED",
                "due_at": int(scheduled.timestamp() * 1000),
                "planned_at": int(ctx.received_at.timestamp() * 1000),
                "priority": request.priority,
                "project": request.project or "",
                "source_message_id": ctx.message_id,
                "source_operation_index": task_index,
                "receipt_id": receipt_id,
            },
        )
        if task_receipt.status != "completed":
            return AssistantResponse(
                text="任务暂时没有创建成功，没有安排提醒。",
                status="failed",
                receipt_id=task_receipt.receipt_id,
            )

        reminder_receipts = ReceiptService(
            store=self._store,
            feishu=self._reminders,
            audit_sink=self._audit_sink_factory(ctx=ctx, kind="reminder", action="create"),
            base_token=self._base_token,
            table_id=self._reminders_table,
            verify_fields=(
                "reminder_id",
                "task_id",
                "scheduled_at",
                "status",
                "receipt_id",
            ),
        )
        reminder_receipt = reminder_receipts.execute_create(
            ctx.message_id,
            reminder_index,
            fields=lambda receipt_id: {
                "reminder_id": reminder_id,
                "task_id": task_id,
                "scheduled_at": int(scheduled.timestamp() * 1000),
                "status": "SCHEDULED",
                "follow_up_count": 0,
                "max_follow_ups": self._max_follow_ups,
                "next_action_at": int(scheduled.timestamp() * 1000),
                "source_message_id": ctx.message_id,
                "source_operation_index": reminder_index,
                "receipt_id": receipt_id,
            },
        )
        if reminder_receipt.status != "completed":
            return AssistantResponse(
                text="任务已创建，但提醒未创建成功。请求已保留，不会重复创建任务。",
                status="failed",
                receipt_id=task_receipt.receipt_id,
            )

        scheduled_utc = scheduled.astimezone(timezone.utc).isoformat()
        self._store.schedule_reminder_job(
            reminder_id=reminder_id,
            task_id=task_id,
            user_id=ctx.user_id,
            status="SCHEDULED",
            scheduled_at=scheduled_utc,
            next_action_at=scheduled_utc,
            follow_up_count=0,
            max_follow_ups=self._max_follow_ups,
        )
        return AssistantResponse(
            text=f"已创建任务并安排提醒：{request.title} · {scheduled:%Y-%m-%d %H:%M}",
            status="completed",
            receipt_id=task_receipt.receipt_id,
        )

    def apply_user_action(self, ctx: MessageContext, action: str) -> AssistantResponse:
        active = {
            "SCHEDULED",
            "SENT",
            "AWAITING_CONFIRMATION",
            "SNOOZED",
        }
        reminders = [
            record
            for record in self._reminders.list_records(self._base_token, self._reminders_table)
            if str(record["fields"].get("status") or "") in active
        ]
        if len(reminders) != 1:
            detail = "没有找到待确认的提醒" if not reminders else "有多个待确认的提醒"
            return AssistantResponse(
                text=f"{detail}，请说明是哪一项？",
                status="needs_clarification",
            )
        reminder = reminders[0]
        fields = reminder["fields"]
        reminder_id = str(fields["reminder_id"])
        task_id = str(fields["task_id"])
        current = ReminderStatus(str(fields["status"]))
        next_status = transition_reminder(
            current,
            action,
            int(fields.get("follow_up_count") or 0),
            int(fields.get("max_follow_ups") or self._max_follow_ups),
        )
        next_time = (
            ctx.received_at + timedelta(minutes=30)
            if next_status is ReminderStatus.SNOOZED
            else ctx.received_at
        )
        reminder_receipts = ReceiptService(
            store=self._store,
            feishu=self._reminders,
            audit_sink=self._audit_sink_factory(ctx=ctx, kind="reminder", action="update"),
            base_token=self._base_token,
            table_id=self._reminders_table,
            verify_fields=("status", "receipt_id"),
        )
        reminder_receipt = reminder_receipts.execute_update(
            ctx.message_id,
            0,
            record_id=reminder["record_id"],
            fields=lambda receipt_id: {
                "status": next_status.value,
                "next_action_at": int(next_time.timestamp() * 1000),
                "receipt_id": receipt_id,
            },
        )
        if reminder_receipt.status != "completed":
            return AssistantResponse(
                text="提醒状态暂时没有更新成功。",
                status="failed",
                receipt_id=reminder_receipt.receipt_id,
            )

        if action in {"complete", "skip"}:
            tasks = [
                record
                for record in self._tasks.list_records(self._base_token, self._tasks_table)
                if str(record["fields"].get("task_id") or "") == task_id
            ]
            if len(tasks) != 1:
                return AssistantResponse(
                    text="提醒已更新，但没有找到唯一关联任务。",
                    status="failed",
                    receipt_id=reminder_receipt.receipt_id,
                )
            current_task = TaskStatus(str(tasks[0]["fields"].get("status") or "PLANNED"))
            task_status = transition_task(current_task, action)
            task_receipts = ReceiptService(
                store=self._store,
                feishu=self._tasks,
                audit_sink=self._audit_sink_factory(ctx=ctx, kind="task", action="update"),
                base_token=self._base_token,
                table_id=self._tasks_table,
                verify_fields=("status", "receipt_id"),
            )
            task_receipt = task_receipts.execute_update(
                ctx.message_id,
                1,
                record_id=tasks[0]["record_id"],
                fields=lambda receipt_id: {
                    "status": task_status.value,
                    "receipt_id": receipt_id,
                },
            )
            if task_receipt.status != "completed":
                return AssistantResponse(
                    text="提醒已更新，但任务状态更新失败。",
                    status="failed",
                    receipt_id=reminder_receipt.receipt_id,
                )

        job = self._store.get_reminder_job(reminder_id)
        self._store.update_reminder_job(
            reminder_id,
            status=next_status.value,
            next_action_at=next_time.astimezone(timezone.utc).isoformat(),
            follow_up_count=job.follow_up_count,
            last_sent_at=job.last_sent_at,
        )
        messages = {
            "complete": "已标记完成，停止提醒。",
            "snooze": "好的，30 分钟后再提醒。",
            "skip": "已标记今天不做，停止提醒。",
            "cancel": "已取消提醒，不再催促。",
        }
        return AssistantResponse(
            text=messages[action],
            status="completed",
            receipt_id=reminder_receipt.receipt_id,
        )
