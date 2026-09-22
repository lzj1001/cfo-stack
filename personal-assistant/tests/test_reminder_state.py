from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

from personal_assistant.state import StateStore


def schedule(store, reminder_id="rem_1", status="SCHEDULED", next_action="2026-09-23T11:00:00+00:00"):
    store.schedule_reminder_job(
        reminder_id=reminder_id,
        task_id="task_1",
        user_id="ou_1",
        status=status,
        scheduled_at="2026-09-23T11:00:00+00:00",
        next_action_at=next_action,
        follow_up_count=0,
        max_follow_ups=1,
    )


def test_reminder_job_survives_restart_without_task_text(tmp_path: Path):
    db = tmp_path / "state.db"
    first = StateStore(db)
    first.migrate()
    schedule(first)
    first.close()
    second = StateStore(db)
    second.migrate()
    assert second.get_reminder_job("rem_1").task_id == "task_1"
    second.close()

    connection = sqlite3.connect(db)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(reminder_jobs)")}
    connection.close()
    assert columns.isdisjoint({"title", "details", "raw_text"})


def test_concurrent_due_claim_has_one_winner(tmp_path: Path):
    db = tmp_path / "state.db"
    initial = StateStore(db)
    initial.migrate()
    schedule(initial)
    initial.close()

    def claim(_):
        store = StateStore(db)
        rows = store.claim_due_reminders(
            "2026-09-23T11:00:01+00:00",
            lease_until="2026-09-23T11:05:00+00:00",
            limit=1,
        )
        store.close()
        return len(rows)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [0, 1]


def test_due_jobs_are_ordered_by_next_action(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    schedule(store, "rem_late", next_action="2026-09-23T11:00:00+00:00")
    schedule(store, "rem_early", next_action="2026-09-23T10:00:00+00:00")
    rows = store.claim_due_reminders(
        "2026-09-23T12:00:00+00:00",
        lease_until="2026-09-23T12:05:00+00:00",
        limit=2,
    )
    assert [row.reminder_id for row in rows] == ["rem_early", "rem_late"]
    store.close()


def test_terminal_jobs_are_not_claimed(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    for status in ("COMPLETED", "SKIPPED", "CANCELLED", "EXHAUSTED"):
        schedule(store, f"rem_{status}", status=status)
    assert store.claim_due_reminders(
        "2026-09-23T12:00:00+00:00",
        lease_until="2026-09-23T12:05:00+00:00",
    ) == []
    store.close()


def test_update_reminder_runtime_state(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    schedule(store)
    store.update_reminder_job(
        "rem_1",
        status="AWAITING_CONFIRMATION",
        next_action_at="2026-09-23T11:30:00+00:00",
        follow_up_count=1,
        last_sent_at="2026-09-23T11:00:00+00:00",
    )
    row = store.get_reminder_job("rem_1")
    assert row.status == "AWAITING_CONFIRMATION"
    assert row.follow_up_count == 1
    assert row.lease_until is None
    store.close()
