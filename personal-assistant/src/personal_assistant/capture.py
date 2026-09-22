from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .errors import ValidationError
from .models import operation_key
from .router import CaptureRequest


@dataclass(frozen=True)
class NormalizedCapture:
    capture_id: str
    source_message_id: str
    operation_index: int
    raw_text: str
    title: str
    capture_type: str
    tags: tuple[str, ...]
    related_topics: tuple[str, ...]
    confidence: Decimal


def normalize_capture(
    message_id: str,
    request: CaptureRequest,
    *,
    raw_text: str,
    confidence: str,
) -> NormalizedCapture:
    try:
        confidence_value = Decimal(confidence)
    except InvalidOperation as error:
        raise ValidationError("capture confidence is invalid") from error
    if not confidence_value.is_finite() or not Decimal("0") <= confidence_value <= Decimal("1"):
        raise ValidationError("capture confidence is invalid")
    return NormalizedCapture(
        capture_id=operation_key(message_id, request.operation_index),
        source_message_id=message_id,
        operation_index=request.operation_index,
        raw_text=raw_text,
        title=request.title.strip(),
        capture_type=request.capture_type,
        tags=tuple(tag.strip() for tag in request.tags if tag.strip()),
        related_topics=tuple(topic.strip() for topic in request.related_topics if topic.strip()),
        confidence=confidence_value,
    )


def to_capture_fields(
    capture: NormalizedCapture, *, receipt_id: str, captured_at_ms: int
) -> dict[str, object]:
    return {
        "capture_id": capture.capture_id,
        "raw_text": capture.raw_text,
        "title": capture.title,
        "capture_type": capture.capture_type,
        "tags": "、".join(capture.tags),
        "related_topics": "、".join(capture.related_topics),
        "source_message_id": capture.source_message_id,
        "source_operation_index": capture.operation_index,
        "model_confidence": capture.confidence,
        "receipt_id": receipt_id,
        "captured_at": captured_at_ms,
    }
