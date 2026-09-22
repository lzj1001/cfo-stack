from datetime import datetime, timedelta, timezone
from pathlib import Path

from personal_assistant.dispatcher import ReminderDispatcher
from personal_assistant.state import StateStore


NOW = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)


def build(tmp_path: Path, send_result=True):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    store.schedule_reminder_job(
        reminder_id="rem_1",
        task_id="task_1",
        user_id="ou_1",
        status="SCHEDULED",
        scheduled_at=NOW.isoformat(),
        next_action_at=NOW.isoformat(),
        follow_up_count=0,
        max_follow_ups=1,
    )
    sends = []
    updates = []

    def send_dm(user_id, text):
        sends.append((user_id, text))
        return send_result

    dispatcher = ReminderDispatcher(
        store=store,
        send_dm=send_dm,
        task_title=lambda task_id: "洗衣服",
        update_remote=lambda reminder_id, fields: updates.append((reminder_id, fields)),
    )
    return dispatcher, store, sends, updates


def test_first_send_waits_for_confirmation(tmp_path: Path):
    dispatcher, store, sends, updates = build(tmp_path)
    dispatcher.run_due(NOW)
    row = store.get_reminder_job("rem_1")
    assert row.status == "AWAITING_CONFIRMATION"
    assert row.last_sent_at == NOW.isoformat()
    assert row.follow_up_count == 0
    assert sends == [("ou_1", "提醒：洗衣服。完成后回复“完成了”，也可以回复“晚点提醒”或“今天不做”。")]
    assert updates[-1][1]["status"] == "AWAITING_CONFIRMATION"
    store.close()


def test_no_reply_allows_one_follow_up_then_exhausts(tmp_path: Path):
    dispatcher, store, sends, _ = build(tmp_path)
    dispatcher.run_due(NOW)
    dispatcher.handle_no_reply("rem_1", NOW + timedelta(minutes=30))
    scheduled = store.get_reminder_job("rem_1")
    assert scheduled.status == "SCHEDULED"
    assert scheduled.follow_up_count == 1

    follow_up_time = NOW + timedelta(hours=1)
    dispatcher.run_due(follow_up_time)
    assert len(sends) == 2
    dispatcher.handle_no_reply("rem_1", follow_up_time + timedelta(minutes=30))
    assert store.get_reminder_job("rem_1").status == "EXHAUSTED"
    dispatcher.run_due(follow_up_time + timedelta(hours=1))
    assert len(sends) == 2
    store.close()


def test_failed_send_remains_retryable_without_follow_up_increment(tmp_path: Path):
    dispatcher, store, sends, updates = build(tmp_path, send_result=False)
    dispatcher.run_due(NOW)
    row = store.get_reminder_job("rem_1")
    assert row.status == "SCHEDULED"
    assert row.follow_up_count == 0
    assert row.next_action_at == (NOW + timedelta(minutes=5)).isoformat()
    assert updates == []
    assert len(sends) == 1
    store.close()


def test_remote_update_failure_after_send_never_resends(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    store.schedule_reminder_job(
        reminder_id="rem_1", task_id="task_1", user_id="ou_1", status="SCHEDULED",
        scheduled_at=NOW.isoformat(), next_action_at=NOW.isoformat(),
        follow_up_count=0, max_follow_ups=1,
    )
    sends = []

    def update_remote(reminder_id, fields):
        raise RuntimeError("remote unavailable")

    dispatcher = ReminderDispatcher(
        store=store,
        send_dm=lambda user_id, text: sends.append(text) or True,
        task_title=lambda task_id: "洗衣服",
        update_remote=update_remote,
    )
    try:
        dispatcher.run_due(NOW)
    except RuntimeError:
        pass
    assert store.get_reminder_job("rem_1").status == "AWAITING_CONFIRMATION"
    dispatcher.run_due(NOW + timedelta(minutes=10))
    assert len(sends) == 1
    store.close()
