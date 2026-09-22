from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from personal_assistant.errors import DuplicateOperation, ValidationError
from personal_assistant.state import StateStore


def test_claim_survives_restart(tmp_path: Path):
    db = tmp_path / "state.db"
    first = StateStore(db)
    first.migrate()
    first.claim("om_1:0", "om_1", 0)
    first.close()

    second = StateStore(db)
    second.migrate()
    with pytest.raises(DuplicateOperation):
        second.claim("om_1:0", "om_1", 0)
    second.close()


def test_concurrent_claim_has_one_winner(tmp_path: Path):
    db = tmp_path / "state.db"
    initial = StateStore(db)
    initial.migrate()
    initial.close()

    def attempt(_):
        store = StateStore(db)
        try:
            store.claim("om_2:0", "om_2", 0)
            return "claimed"
        except DuplicateOperation:
            return "duplicate"
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["claimed", "duplicate"]


def test_business_success_can_leave_audit_pending(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    store.claim("om_3:0", "om_3", 0)
    store.mark_business_succeeded("om_3:0", target_id="rec_1", receipt_id="r_1")
    row = store.get_operation("om_3:0")
    assert row.status == "business_succeeded"
    assert row.audit_status == "pending"
    assert row.target_id == "rec_1"
    store.close()


def test_failed_operation_retry_count_is_bounded(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    store.claim("om_4:0", "om_4", 0)
    assert store.mark_failed("om_4:0", error_code="timeout", max_retries=1).retry_count == 1
    with pytest.raises(ValidationError, match="retry limit"):
        store.mark_failed("om_4:0", error_code="timeout", max_retries=1)
    store.close()


def test_business_success_cannot_be_marked_failed(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    store.claim("om_5:0", "om_5", 0)
    store.mark_business_succeeded("om_5:0", target_id="rec_5", receipt_id="r_5")
    with pytest.raises(ValidationError, match="invalid transition"):
        store.mark_failed("om_5:0", error_code="late_error", max_retries=2)
    store.close()


def test_model_metadata_has_no_prompt_or_secret_columns(tmp_path: Path):
    db = tmp_path / "state.db"
    store = StateStore(db)
    store.migrate()
    store.record_model_call(
        call_id="call_1",
        provider="siliconflow",
        model="router-model",
        latency_ms=25,
        input_tokens=10,
        output_tokens=4,
        result="success",
    )
    store.close()

    connection = sqlite3.connect(db)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(model_calls)")}
    row = connection.execute(
        "SELECT provider, model, latency_ms, input_tokens, output_tokens, result FROM model_calls"
    ).fetchone()
    connection.close()

    assert columns.isdisjoint({"prompt", "messages", "api_key", "secret"})
    assert row == ("siliconflow", "router-model", 25, 10, 4, "success")


def test_queue_and_cursor_survive_restart_without_business_payload(tmp_path: Path):
    db = tmp_path / "state.db"
    first = StateStore(db)
    first.migrate()
    first.enqueue(
        queue_id="q_1",
        operation_key="om_6:0",
        action="retry_audit",
        available_at="2026-09-22T12:00:00+00:00",
    )
    first.set_cursor("feishu_messages", "cursor_123")
    first.close()

    second = StateStore(db)
    second.migrate()
    queued = second.next_due("2026-09-22T12:00:01+00:00")
    assert queued.queue_id == "q_1"
    assert queued.action == "retry_audit"
    assert second.get_cursor("feishu_messages") == "cursor_123"
    second.complete_queue("q_1")
    assert second.next_due("2026-09-22T12:00:01+00:00") is None
    second.close()

    connection = sqlite3.connect(db)
    queue_columns = {row[1] for row in connection.execute("PRAGMA table_info(queue)")}
    connection.close()
    assert queue_columns.isdisjoint({"payload", "raw_input", "transaction", "task"})
