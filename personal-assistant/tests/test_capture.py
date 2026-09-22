from decimal import Decimal

from pydantic import ValidationError as PydanticValidationError
import pytest

from personal_assistant.capture import normalize_capture, to_capture_fields
from personal_assistant.router import CaptureRequest


def test_capture_preserves_raw_text_and_limits_metadata():
    raw = "突然想到可以做一个给英语不好的人总结海外 AI 内容的账号"
    request = CaptureRequest(
        operation_index=0,
        title="海外 AI 中文总结账号",
        capture_type="idea",
        tags=["AI", "内容", "中文"],
        related_topics=["自媒体"],
    )
    capture = normalize_capture("om_cap", request, raw_text=raw, confidence="0.96")
    fields = to_capture_fields(capture, receipt_id="rcpt_cap", captured_at_ms=1)
    assert capture.capture_id == "om_cap:0"
    assert fields["raw_text"] == raw
    assert fields["tags"] == "AI、内容、中文"
    assert "task_id" not in fields


def test_more_than_five_tags_is_rejected():
    with pytest.raises(PydanticValidationError):
        CaptureRequest(
            operation_index=0,
            title="idea",
            capture_type="idea",
            tags=["1", "2", "3", "4", "5", "6"],
            related_topics=[],
        )


def test_invalid_capture_type_is_rejected():
    with pytest.raises(PydanticValidationError):
        CaptureRequest(
            operation_index=0,
            title="x",
            capture_type="task",
            tags=[],
            related_topics=[],
        )
