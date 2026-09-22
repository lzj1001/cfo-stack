from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from personal_assistant.capture_service import CaptureService
from personal_assistant.feishu import FeishuAdapter
from personal_assistant.models import MessageContext
from personal_assistant.router import CaptureRequest
from personal_assistant.state import StateStore


class MemoryTransport:
    def __init__(self):
        self.records = {}
        self.posts = 0

    def __call__(self, method, path, json=None):
        if method == "POST":
            self.posts += 1
            record_id = f"rec_{self.posts}"
            self.records[record_id] = dict(json["fields"])
            return {"record_id": record_id}
        record_id = path.rsplit("/", 1)[-1]
        return {"record_id": record_id, "fields": self.records[record_id]}


def test_capture_write_is_verified_and_idempotent(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    transport = MemoryTransport()
    service = CaptureService(
        store=store,
        adapter=FeishuAdapter(transport),
        base_token="base",
        table_id="captures",
        audit_sink_factory=lambda **_: lambda receipt: None,
    )
    ctx = MessageContext(
        message_id="om_cap",
        user_id="ou_1",
        text="突然想到做海外AI中文总结",
        received_at=datetime(2026, 9, 22, 20, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    request = CaptureRequest(
        operation_index=0,
        title="海外 AI 中文总结",
        capture_type="idea",
        tags=["AI", "内容"],
        related_topics=["自媒体"],
    )
    first = service.capture(ctx, request, confidence="0.96")
    second = service.capture(ctx, request, confidence="0.96")
    assert first.status == second.status == "completed"
    assert first.receipt_id == second.receipt_id
    assert transport.posts == 1
    assert next(iter(transport.records.values()))["raw_text"] == ctx.text
    store.close()
