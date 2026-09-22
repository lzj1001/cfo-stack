import pytest

from personal_assistant.errors import ValidationError
from personal_assistant.task_schema import build_task_schema_plan


def complete_snapshot():
    task_names = {
        "task_id", "title", "details", "status", "due_at", "planned_at",
        "priority", "project", "source_message_id", "source_operation_index",
        "receipt_id", "created_at", "updated_at",
    }
    reminder_names = {
        "reminder_id", "task_id", "scheduled_at", "status", "follow_up_count",
        "max_follow_ups", "last_sent_at", "next_action_at", "source_message_id",
        "source_operation_index", "receipt_id", "created_at", "updated_at",
    }
    return {"tables": {
        "Tasks": {"fields": {name: {"type": "text"} for name in task_names}},
        "Reminders": {"fields": {name: {"type": "text"} for name in reminder_names}},
    }}


def test_missing_tables_produce_two_create_operations():
    plan = build_task_schema_plan({"tables": {}})
    assert [table.name for table in plan.create_tables] == ["Tasks", "Reminders"]


def test_complete_tables_produce_empty_plan():
    assert build_task_schema_plan(complete_snapshot()).is_empty


def test_existing_table_missing_field_is_rejected():
    snapshot = complete_snapshot()
    del snapshot["tables"]["Tasks"]["fields"]["task_id"]
    with pytest.raises(ValidationError, match="Tasks"):
        build_task_schema_plan(snapshot)
