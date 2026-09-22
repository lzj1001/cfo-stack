import asyncio
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from personal_assistant.accounting import normalize_item
from personal_assistant.app import AssistantApp
from personal_assistant.errors import LLMUnavailableError
from personal_assistant.models import AssistantResponse, MessageContext
from personal_assistant.router import ExecutionGate, RouterDecision


def context(text, message_id="om_app"):
    return MessageContext(
        message_id=message_id,
        user_id="ou_1",
        text=text,
        received_at=datetime(2026, 9, 22, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def accounting_decision():
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
                }
            ],
            "correction": None,
            "ambiguities": [],
            "reason_code": "explicit_expense",
        }
    )


class FakeRouter:
    def __init__(self, result):
        self.result = result
        self.raise_timeout = False

    async def route(self, ctx):
        if self.raise_timeout:
            raise LLMUnavailableError("timeout")
        return self.result


class FakeAccounting:
    def __init__(self):
        self.write_count = 0

    def record(self, ctx, decision):
        self.write_count += len(decision.items)
        return AssistantResponse(text="已记 1 笔：\n- 午饭 ¥35 · 待确认", status="completed", receipt_id="r1")

    def correct(self, ctx, correction):
        self.write_count += 1
        return AssistantResponse(text="已更正", status="completed", receipt_id="r2")


def test_accounting_route_executes_service():
    accounting = FakeAccounting()
    app = AssistantApp(router=FakeRouter(accounting_decision()), accounting=accounting)
    response = asyncio.run(app.handle_message(context("午饭35")))
    assert response.status == "completed"
    assert response.text.startswith("已记 1 笔")
    assert accounting.write_count == 1


def test_ambiguity_returns_one_question_without_side_effect():
    accounting = FakeAccounting()
    ambiguous = accounting_decision().model_copy(
        update={
            "items": [
                accounting_decision().items[0].model_copy(
                    update={"description": "给小王", "amount": "500"}
                )
            ]
        }
    )
    app = AssistantApp(router=FakeRouter(ambiguous), accounting=accounting)
    response = asyncio.run(app.handle_message(context("给小王500")))
    assert response.status == "needs_clarification"
    assert response.text.count("？") == 1
    assert accounting.write_count == 0


def test_llm_timeout_returns_failed_without_accounting_call():
    router = FakeRouter(accounting_decision())
    router.raise_timeout = True
    accounting = FakeAccounting()
    response = asyncio.run(
        AssistantApp(router=router, accounting=accounting).handle_message(context("午饭35"))
    )
    assert response.status == "failed"
    assert accounting.write_count == 0


def test_non_accounting_intent_has_no_side_effect():
    chat = RouterDecision.model_validate(
        {
            **accounting_decision().model_dump(mode="json"),
            "intent": "CHAT",
            "items": [],
            "confidence": "0.95",
            "reason_code": "chat",
        }
    )
    accounting = FakeAccounting()
    response = asyncio.run(
        AssistantApp(router=FakeRouter(chat), accounting=accounting).handle_message(context("你好"))
    )
    assert response.status == "chat"
    assert accounting.write_count == 0


def test_literal_acceptance_fixtures_validate_types_amounts_and_gates():
    fixture_path = Path(__file__).parent / "fixtures" / "accounting_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert {case["id"] for case in cases} == {1, 2, 3, 4, 5, 6, 7, 8, 14, 15, 16}

    for case in cases:
        if "router" not in case:
            assert case["mutations"] == 0
            continue
        decision = RouterDecision.model_validate(case["router"])
        gate = ExecutionGate.evaluate(decision, case["text"])
        if case["status"] == "needs_clarification":
            assert gate.execute is False
            assert case["mutations"] == 0
            continue
        normalized = [
            normalize_item("fixture", item, raw_text=case["text"], confidence=decision.confidence)
            for item in decision.items
        ]
        assert [item.transaction_type for item in normalized] == case["types"]
        assert [item.amount_fen for item in normalized] == case["fen"]
