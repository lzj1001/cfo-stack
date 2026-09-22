from pathlib import Path
from types import SimpleNamespace

from personal_assistant.audit import ReceiptService
from personal_assistant.errors import FeishuWriteError
from personal_assistant.state import StateStore


class FakeFeishu:
    def __init__(self, *, fail=False, uncertain_once=False):
        self.fail = fail
        self.uncertain_once = uncertain_once
        self.creates = []
        self.verifies = []

    def create_verified(self, base_token, table_id, fields, *, verify_fields):
        self.creates.append((base_token, table_id, fields, verify_fields))
        if self.fail:
            raise FeishuWriteError("write failed")
        if self.uncertain_once:
            self.uncertain_once = False
            raise FeishuWriteError("readback failed", record_id="rec_1")
        return SimpleNamespace(record_id="rec_1", fields=fields, verified=True)

    def verify_existing(self, base_token, table_id, record_id, fields, *, verify_fields):
        self.verifies.append((base_token, table_id, record_id, fields, verify_fields))
        return SimpleNamespace(record_id=record_id, fields=fields, verified=True)


class FailOnceAudit:
    def __init__(self):
        self.attempts = 0
        self.records = []

    def __call__(self, receipt):
        self.attempts += 1
        if self.attempts == 1:
            raise FeishuWriteError("audit unavailable")
        self.records.append(receipt)


def build_service(tmp_path: Path, *, fail_write=False, uncertain_once=False, audit=None):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    feishu = FakeFeishu(fail=fail_write, uncertain_once=uncertain_once)
    audit_sink = audit or (lambda receipt: None)
    service = ReceiptService(
        store=store,
        feishu=feishu,
        audit_sink=audit_sink,
        base_token="base",
        table_id="table",
        verify_fields=("来源消息 ID",),
    )
    return service, feishu, store


def test_replay_after_business_success_does_not_create_again(tmp_path: Path):
    audit = FailOnceAudit()
    service, feishu, store = build_service(tmp_path, audit=audit)

    first = service.execute_create("om_1", 0, fields={"来源消息 ID": "om_1"})
    second = service.execute_create("om_1", 0, fields={"来源消息 ID": "om_1"})

    assert first.target_id == second.target_id == "rec_1"
    assert first.audit_status == "pending"
    assert len(feishu.creates) == 1
    assert second.status == "completed"
    assert second.audit_status == "completed"
    assert audit.attempts == 2
    assert len(audit.records) == 1
    store.close()


def test_write_failure_never_returns_completed(tmp_path: Path):
    service, feishu, store = build_service(tmp_path, fail_write=True)
    receipt = service.execute_create("om_2", 0, fields={"来源消息 ID": "om_2"})
    assert receipt.status == "failed"
    assert receipt.target_id is None
    assert len(feishu.creates) == 1
    store.close()


def test_replay_recovers_uncertain_create_by_readback_without_second_post(tmp_path: Path):
    service, feishu, store = build_service(tmp_path, uncertain_once=True)

    first = service.execute_create("om_uncertain", 0, fields={"来源消息 ID": "om_uncertain"})
    second = service.execute_create("om_uncertain", 0, fields={"来源消息 ID": "om_uncertain"})

    assert first.status == "failed"
    assert second.status == "completed"
    assert first.receipt_id == second.receipt_id
    assert len(feishu.creates) == 1
    assert len(feishu.verifies) == 1
    assert store.get_operation("om_uncertain:0").status == "completed"
    store.close()


def test_fields_factory_receives_receipt_id(tmp_path: Path):
    service, feishu, store = build_service(tmp_path)
    seen = []
    receipt = service.execute_create(
        "om_factory",
        0,
        fields=lambda receipt_id: seen.append(receipt_id) or {"来源消息 ID": "om_factory"},
    )
    assert seen == [receipt.receipt_id]
    assert feishu.creates[0][2]["来源消息 ID"] == "om_factory"
    store.close()
