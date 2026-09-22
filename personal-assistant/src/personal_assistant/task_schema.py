from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError


@dataclass(frozen=True)
class TableDefinition:
    name: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TaskSchemaPlan:
    create_tables: tuple[TableDefinition, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.create_tables


TASK_FIELDS = (
    ("task_id", "text"), ("title", "text"), ("details", "text"),
    ("status", "select"), ("due_at", "datetime"), ("planned_at", "datetime"),
    ("priority", "select"), ("project", "text"), ("source_message_id", "text"),
    ("source_operation_index", "number"), ("receipt_id", "text"),
    ("created_at", "created_at"), ("updated_at", "updated_at"),
)

REMINDER_FIELDS = (
    ("reminder_id", "text"), ("task_id", "text"), ("scheduled_at", "datetime"),
    ("status", "select"), ("follow_up_count", "number"), ("max_follow_ups", "number"),
    ("last_sent_at", "datetime"), ("next_action_at", "datetime"),
    ("source_message_id", "text"), ("source_operation_index", "number"),
    ("receipt_id", "text"), ("created_at", "created_at"), ("updated_at", "updated_at"),
)


def build_task_schema_plan(snapshot: dict) -> TaskSchemaPlan:
    tables = snapshot.get("tables") or {}
    missing = []
    for name, fields in (("Tasks", TASK_FIELDS), ("Reminders", REMINDER_FIELDS)):
        table = tables.get(name)
        if table is None:
            missing.append(TableDefinition(name, fields))
            continue
        existing = set((table.get("fields") or {}).keys())
        required = {field_name for field_name, _ in fields}
        if existing != required:
            raise ValidationError(f"{name} table conflicts with required schema")
    return TaskSchemaPlan(tuple(missing))
