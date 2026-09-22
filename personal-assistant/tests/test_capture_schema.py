import pytest

from personal_assistant.capture_schema import build_capture_schema_plan
from personal_assistant.errors import ValidationError


FIELDS = {
    "capture_id", "raw_text", "title", "capture_type", "tags", "related_topics",
    "source_message_id", "source_operation_index", "model_confidence", "receipt_id",
    "captured_at", "created_at",
}


def test_missing_capture_table_produces_create():
    assert build_capture_schema_plan({"tables": {}}).create_table is True


def test_complete_capture_table_is_empty():
    snapshot = {"tables": {"Captures": {"fields": {name: {"type": "text"} for name in FIELDS}}}}
    assert build_capture_schema_plan(snapshot).is_empty


def test_conflicting_capture_table_is_rejected():
    with pytest.raises(ValidationError):
        build_capture_schema_plan({"tables": {"Captures": {"fields": {"capture_id": {"type": "text"}}}}})
