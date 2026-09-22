from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from personal_assistant.accounting_service import AccountingService, RemoteAuditSink
from personal_assistant.errors import ValidationError
from personal_assistant.feishu import FeishuAdapter
from personal_assistant.models import MessageContext, Receipt
from personal_assistant.router import CorrectionRequest, RouterDecision
from personal_assistant.state import StateStore


class MemoryTransport:
    def __init__(self):
        self.records = {}
        self.post_count = 0
        self.put_count = 0
        self.fail_on_post = None

    def __call__(self, method, path, json=None):
        if method == "POST":
            self.post_count += 1
            if self.fail_on_post == self.post_count:
                raise RuntimeError("write failed")
            record_id = f"rec_{self.post_count}"
            self.records[record_id] = dict(json["fields"])
            return {"record_id": record_id}
        if method == "PUT":
            self.put_count += 1
            record_id = path.rsplit("/", 1)[-1]
            self.records[record_id].update(json["fields"])
            return {"record_id": record_id}
        tail = path.rsplit("/", 1)[-1]
        if tail == "records":
            return {
                "records": [
                    {"record_id": record_id, "fields": fields}
                    for record_id, fields in self.records.items()
                ]
            }
        return {"record_id": tail, "fields": self.records[tail]}


def context(text, message_id="om_multi"):
    return MessageContext(
        message_id=message_id,
        user_id="ou_1",
        text=text,
        received_at=datetime(2026, 9, 22, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def decision_for_two_items():
    return RouterDecision.model_validate(
        {
            "schema_version": "router-v1",
            "intent": "ACCOUNTING",
            "confidence": "0.97",
            "items": [
                {
                    "operation_index": 0,
                    "description": "午饭",
                    "amount": "35",
                    "currency": "CNY",
                    "transaction_type": "EXPENSE",
                },
                {
                    "operation_index": 1,
                    "description": "狗粮",
                    "amount": "280",
                    "currency": "CNY",
                    "transaction_type": "EXPENSE",
                },
            ],
            "correction": None,
            "ambiguities": [],
            "reason_code": "explicit_expense",
        }
    )


def build_service(tmp_path: Path, transport=None, audit_sink_factory=None):
    transport = transport or MemoryTransport()
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    service = AccountingService(
        store=store,
        daily=FeishuAdapter(transport),
        base_token="base",
        daily_table_id="daily",
        model_name="gpt-5.6-luna",
        audit_sink_factory=audit_sink_factory or (lambda **_: lambda receipt: None),
    )
    return service, transport, store


def test_two_items_create_two_verified_records_and_one_response(tmp_path: Path):
    service, transport, store = build_service(tmp_path)
    response = service.record(context("午饭35，狗粮280"), decision_for_two_items())
    assert response.status == "completed"
    assert transport.post_count == 2
    assert response.text == "已记 2 笔：\n- 午饭 ¥35 · 待确认\n- 狗粮 ¥280 · 待确认"
    store.close()


def test_same_message_replay_creates_nothing(tmp_path: Path):
    service, transport, store = build_service(tmp_path)
    first = service.record(context("午饭35，狗粮280"), decision_for_two_items())
    second = service.record(context("午饭35，狗粮280"), decision_for_two_items())
    assert second.status == "completed"
    assert transport.post_count == 2
    assert second.receipt_id == first.receipt_id
    store.close()


def test_second_item_failure_never_claims_full_success(tmp_path: Path):
    transport = MemoryTransport()
    transport.fail_on_post = 2
    service, transport, store = build_service(tmp_path, transport)
    response = service.record(context("午饭35，狗粮280"), decision_for_two_items())
    assert response.status == "failed"
    assert "狗粮" in response.text
    assert "已记 2 笔" not in response.text
    assert transport.post_count == 2
    store.close()


def test_unique_correction_updates_original_without_difference_record(tmp_path: Path):
    service, transport, store = build_service(tmp_path)
    transport.records["rec_dog"] = {
        "交易ID": "original:0",
        "原始口述": "狗粮280",
        "金额": Decimal("280"),
        "回执ID": "old_receipt",
    }
    correction = CorrectionRequest(
        operation_index=0, match_text="狗粮", old_amount="280", new_amount="260"
    )
    response = service.correct(context("刚才狗粮不是280，是260", "om_correct"), correction)
    assert response.status == "completed"
    assert transport.records["rec_dog"]["金额"] == Decimal("260")
    assert transport.records["rec_dog"]["交易ID"] == "original:0"
    assert transport.post_count == 0
    assert transport.put_count == 1
    store.close()


def test_multiple_correction_candidates_ask_without_update(tmp_path: Path):
    service, transport, store = build_service(tmp_path)
    for suffix in ("a", "b"):
        transport.records[f"rec_{suffix}"] = {
            "交易ID": f"old:{suffix}",
            "原始口述": "狗粮280",
            "金额": Decimal("280"),
        }
    response = service.correct(
        context("刚才狗粮不是280，是260", "om_correct_multi"),
        CorrectionRequest(
            operation_index=0, match_text="狗粮", old_amount="280", new_amount="260"
        ),
    )
    assert response.status == "needs_clarification"
    assert transport.put_count == 0
    store.close()


def test_zero_correction_candidates_ask_without_create(tmp_path: Path):
    service, transport, store = build_service(tmp_path)
    response = service.correct(
        context("刚才狗粮不是280，是260", "om_correct_none"),
        CorrectionRequest(
            operation_index=0, match_text="狗粮", old_amount="280", new_amount="260"
        ),
    )
    assert response.status == "needs_clarification"
    assert transport.post_count == 0
    assert transport.put_count == 0
    store.close()


def test_remote_audit_sink_is_idempotent_by_receipt_id():
    transport = MemoryTransport()
    sink = RemoteAuditSink(
        adapter=FeishuAdapter(transport),
        base_token="base",
        audit_table_id="audit",
        context={
            "raw_input": "午饭35",
            "intent": "ACCOUNTING",
            "model_name": "gpt-5.6-luna",
            "model_confidence": Decimal("0.97"),
            "action": "create",
        },
    )
    receipt = Receipt.model_validate(
        {
            "receipt_id": "rcpt_audit",
            "operation_key": "om_audit:0",
            "message_id": "om_audit",
            "operation_index": 0,
            "status": "completed",
            "target_id": "rec_target",
            "error_code": None,
            "audit_status": "pending",
            "created_at": datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
            "updated_at": datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
        }
    )
    sink(receipt)
    sink(receipt)
    assert transport.post_count == 1


def test_accounting_service_requires_audit_sink_factory(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    with pytest.raises(ValidationError, match="audit sink"):
        AccountingService(
            store=store,
            daily=FeishuAdapter(MemoryTransport()),
            base_token="base",
            daily_table_id="daily",
            model_name="gpt-5.6-luna",
            audit_sink_factory=None,
        )
    store.close()


def test_remote_audit_float_readback_matches_decimal_confidence():
    transport = MemoryTransport()
    transport.records["existing"] = {
        "回执ID": "rcpt_float",
        "消息ID": "om_float",
        "来源分项序号": 0,
        "原始输入": "午饭35",
        "意图": "ACCOUNTING",
        "模型名称": "gpt-5.6-luna",
        "模型置信度": 0.97,
        "操作": "create",
        "目标ID": "rec_target",
        "结果": "succeeded",
        "错误代码": "",
    }
    sink = RemoteAuditSink(
        adapter=FeishuAdapter(transport),
        base_token="base",
        audit_table_id="audit",
        context={
            "raw_input": "午饭35",
            "intent": "ACCOUNTING",
            "model_name": "gpt-5.6-luna",
            "model_confidence": Decimal("0.97"),
            "action": "create",
        },
    )
    receipt = Receipt(
        receipt_id="rcpt_float",
        operation_key="om_float:0",
        message_id="om_float",
        operation_index=0,
        status="completed",
        target_id="rec_target",
        audit_status="pending",
        created_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
        updated_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
    )
    sink(receipt)
    assert transport.post_count == 0


def test_remote_audit_converts_string_confidence_to_decimal():
    transport = MemoryTransport()
    sink = RemoteAuditSink(
        adapter=FeishuAdapter(transport),
        base_token="base",
        audit_table_id="audit",
        context={
            "raw_input": "记录一个想法：测试",
            "intent": "CAPTURE",
            "model_name": "router-model",
            "model_confidence": "1.00",
            "action": "create",
        },
    )
    receipt = Receipt(
        receipt_id="rcpt_capture",
        operation_key="om_capture:0",
        message_id="om_capture",
        operation_index=0,
        status="completed",
        target_id="rec_capture",
        audit_status="pending",
        created_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
        updated_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
    )

    sink(receipt)

    assert transport.records["rec_1"]["模型置信度"] == Decimal("1.00")


def test_remote_audit_accepts_numeric_strings_from_feishu():
    transport = MemoryTransport()
    transport.records["existing"] = {
        "回执ID": "rcpt_string_number",
        "消息ID": "om_string_number",
        "来源分项序号": "0",
        "原始输入": "记录一个想法：测试",
        "意图": "CAPTURE",
        "模型名称": "router-model",
        "模型置信度": "1",
        "操作": "create",
        "目标ID": "rec_capture",
        "结果": "succeeded",
        "错误代码": "",
    }
    sink = RemoteAuditSink(
        adapter=FeishuAdapter(transport),
        base_token="base",
        audit_table_id="audit",
        context={
            "raw_input": "记录一个想法：测试",
            "intent": "CAPTURE",
            "model_name": "router-model",
            "model_confidence": "1.00",
            "action": "create",
        },
    )
    receipt = Receipt(
        receipt_id="rcpt_string_number",
        operation_key="om_string_number:0",
        message_id="om_string_number",
        operation_index=0,
        status="completed",
        target_id="rec_capture",
        audit_status="pending",
        created_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
        updated_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
    )

    sink(receipt)

    assert transport.post_count == 0


def test_remote_audit_accepts_omitted_empty_error_code():
    transport = MemoryTransport()
    transport.records["existing"] = {
        "回执ID": "rcpt_omitted_empty",
        "消息ID": "om_omitted_empty",
        "来源分项序号": 0,
        "原始输入": "记录一个想法：测试",
        "意图": "CAPTURE",
        "模型名称": "router-model",
        "模型置信度": 1,
        "操作": "create",
        "目标ID": "rec_capture",
        "结果": "succeeded",
    }
    sink = RemoteAuditSink(
        adapter=FeishuAdapter(transport),
        base_token="base",
        audit_table_id="audit",
        context={
            "raw_input": "记录一个想法：测试",
            "intent": "CAPTURE",
            "model_name": "router-model",
            "model_confidence": "1",
            "action": "create",
        },
    )
    receipt = Receipt(
        receipt_id="rcpt_omitted_empty",
        operation_key="om_omitted_empty:0",
        message_id="om_omitted_empty",
        operation_index=0,
        status="completed",
        target_id="rec_capture",
        audit_status="pending",
        created_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
        updated_at=datetime(2026, 9, 22, 4, tzinfo=ZoneInfo("UTC")),
    )

    sink(receipt)

    assert transport.post_count == 0
