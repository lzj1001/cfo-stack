import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from personal_assistant.audit import ReceiptService
from personal_assistant.config import Settings
from personal_assistant.feishu import FeishuAdapter
from personal_assistant.llm import SiliconFlowClient
from personal_assistant.state import StateStore


class Routed(BaseModel):
    model_config = ConfigDict(strict=True)

    intent: str
    confidence: float


ENV = {
    "SILICONFLOW_API_KEY": "smoke-only-secret",
    "SILICONFLOW_BASE_URL": "https://api.siliconflow.cn/v1",
    "PA_ROUTER_MODEL": "router-model",
    "PA_SUMMARY_MODEL": "summary-model",
    "PA_TIMEOUT_SECONDS": "30",
    "PA_MAX_RETRIES": "2",
    "PA_TIMEZONE": "Asia/Shanghai",
    "PA_DAILY_REVIEW_TIME": "23:30",
    "PA_MAX_FOLLOW_UPS": "1",
    "FEISHU_APP_ID": "cli_smoke",
    "FEISHU_APP_SECRET": "feishu-smoke-secret",
    "PA_BASE_TOKEN": "base_smoke",
    "PA_DAILY_TABLE_ID": "tbl_daily",
    "PA_AUDIT_TABLE_ID": "",
    "PA_TASKS_TABLE_ID": "tbl_tasks",
    "PA_REMINDERS_TABLE_ID": "tbl_reminders",
    "PA_CAPTURES_TABLE_ID": "tbl_captures",
    "PA_REVIEWS_TABLE_ID": "tbl_reviews",
}


class FakeBaseTransport:
    def __init__(self):
        self.posts = 0
        self.records = {}

    def __call__(self, method, path, json=None):
        if method == "POST":
            self.posts += 1
            record_id = f"rec_{self.posts}"
            self.records[record_id] = dict(json["fields"])
            return {"record_id": record_id}
        record_id = path.rsplit("/", 1)[-1]
        return {"record_id": record_id, "fields": self.records[record_id]}


def build_receipt_service(db: Path, transport, audits):
    store = StateStore(db)
    store.migrate()
    service = ReceiptService(
        store=store,
        feishu=FeishuAdapter(transport),
        audit_sink=audits.append,
        base_token="base",
        table_id="daily",
        verify_fields=("来源消息 ID",),
    )
    return service, store


def test_foundation_smoke_without_network(tmp_path: Path):
    settings = Settings.from_env(ENV)
    llm = SiliconFlowClient(
        post=lambda **_: {
            "status": 200,
            "content": '{"intent":"ACCOUNTING","confidence":0.97}',
            "usage": None,
        },
        max_retries=settings.max_retries,
    )
    routed = asyncio.run(
        llm.structured(
            [{"role": "user", "content": "午饭35"}], Routed, model=settings.router_model
        )
    )
    transport = FakeBaseTransport()
    audits = []
    service, store = build_receipt_service(tmp_path / "state.db", transport, audits)
    receipt = service.execute_create("om_smoke", 0, fields={"来源消息 ID": "om_smoke"})

    assert routed.intent == "ACCOUNTING"
    assert receipt.status == "completed"
    assert receipt.audit_status == "completed"
    assert transport.posts == 1
    assert len(audits) == 1
    store.close()


def test_restart_replay_does_not_post_again(tmp_path: Path):
    db = tmp_path / "state.db"
    transport = FakeBaseTransport()
    audits = []
    first, first_store = build_receipt_service(db, transport, audits)
    original = first.execute_create("om_restart", 0, fields={"来源消息 ID": "om_restart"})
    first_store.close()

    second, second_store = build_receipt_service(db, transport, audits)
    replay = second.execute_create("om_restart", 0, fields={"来源消息 ID": "om_restart"})

    assert replay.receipt_id == original.receipt_id
    assert replay.target_id == original.target_id
    assert transport.posts == 1
    second_store.close()
