from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from personal_assistant.feishu import FeishuAdapter
from personal_assistant.models import MessageContext
from personal_assistant.router import TaskReminderRequest
from personal_assistant.state import StateStore
from personal_assistant.task_service import TaskReminderService


class MemoryTransport:
    def __init__(self, fail_on_post=None):
        self.records = {}
        self.post_count = 0
        self.put_count = 0
        self.fail_on_post = fail_on_post

    def __call__(self, method, path, json=None):
        if method == "POST":
            self.post_count += 1
            if self.post_count == self.fail_on_post:
                raise RuntimeError("write failed")
            record_id = f"rec_{self.post_count}"
            self.records[record_id] = dict(json["fields"])
            return {"record_id": record_id}
        if method == "PUT":
            self.put_count += 1
            record_id = path.rsplit("/", 1)[-1]
            self.records[record_id].update(json["fields"])
            return {"record_id": record_id}
        record_id = path.rsplit("/", 1)[-1]
        if record_id == "records":
            return {"records": [{"record_id": key, "fields": value} for key, value in self.records.items()]}
        return {"record_id": record_id, "fields": self.records[record_id]}


def ctx(message_id="om_remind"):
    return MessageContext(
        message_id=message_id,
        user_id="ou_1",
        text="明天晚上提醒我洗衣服",
        received_at=datetime(2026, 9, 22, 18, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def request():
    return TaskReminderRequest(
        operation_index=0,
        title="洗衣服",
        date_reference="tomorrow",
        daypart="evening",
    )


def build(tmp_path: Path, reminder_fail=None):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    tasks = MemoryTransport()
    reminders = MemoryTransport(fail_on_post=reminder_fail)
    service = TaskReminderService(
        store=store,
        tasks=FeishuAdapter(tasks),
        reminders=FeishuAdapter(reminders),
        base_token="base",
        tasks_table_id="tasks",
        reminders_table_id="reminders",
        max_follow_ups=1,
        audit_sink_factory=lambda **_: lambda receipt: None,
    )
    return service, tasks, reminders, store


def test_create_task_and_reminder(tmp_path: Path):
    service, tasks, reminders, store = build(tmp_path)
    response = service.create(ctx(), request())
    assert response.status == "completed"
    assert tasks.post_count == 1
    assert reminders.post_count == 1
    reminder = next(iter(reminders.records.values()))
    assert reminder["scheduled_at"] == int(datetime(2026, 9, 23, 19, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    assert store.get_reminder_job("om_remind:1").status == "SCHEDULED"
    store.close()


def test_duplicate_replay_creates_neither_again(tmp_path: Path):
    service, tasks, reminders, store = build(tmp_path)
    first = service.create(ctx(), request())
    second = service.create(ctx(), request())
    assert second.status == "completed"
    assert second.receipt_id == first.receipt_id
    assert tasks.post_count == reminders.post_count == 1
    store.close()


def test_task_success_reminder_failure_is_partial(tmp_path: Path):
    service, tasks, reminders, store = build(tmp_path, reminder_fail=1)
    response = service.create(ctx(), request())
    assert response.status == "failed"
    assert "任务已创建" in response.text
    assert "提醒未创建" in response.text
    assert tasks.post_count == reminders.post_count == 1
    store.close()
