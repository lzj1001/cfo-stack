from dataclasses import dataclass

from .errors import ValidationError


REQUIRED_FIELDS = {
    "capture_id", "raw_text", "title", "capture_type", "tags", "related_topics",
    "source_message_id", "source_operation_index", "model_confidence", "receipt_id",
    "captured_at", "created_at",
}


@dataclass(frozen=True)
class CaptureSchemaPlan:
    create_table: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.create_table


def build_capture_schema_plan(snapshot: dict) -> CaptureSchemaPlan:
    table = (snapshot.get("tables") or {}).get("Captures")
    if table is None:
        return CaptureSchemaPlan(True)
    names = set((table.get("fields") or {}).keys())
    if names != REQUIRED_FIELDS:
        raise ValidationError("Captures table conflicts with required schema")
    return CaptureSchemaPlan()
