import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError as PydanticValidationError
import pytest

from personal_assistant.models import Intent, MessageContext
from personal_assistant.router import ExecutionGate, Router, RouterDecision


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def structured(self, messages, schema, *, model):
        self.calls.append((messages, schema, model))
        return schema.model_validate(self.payload)


def context(text):
    return MessageContext(
        message_id="om_1",
        user_id="ou_1",
        text=text,
        received_at=datetime(2026, 9, 22, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def decision(**changes):
    payload = {
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
    payload.update(changes)
    return RouterDecision.model_validate(payload)


def test_router_returns_versioned_accounting_items():
    llm = FakeLLM(
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
    result = asyncio.run(
        Router(llm, model="router-model").route(context("午饭35，狗粮280"))
    )
    assert result.intent is Intent.ACCOUNTING
    assert [item.operation_index for item in result.items] == [0, 1]
    assert llm.calls[0][2] == "router-model"
    assert "午饭35，狗粮280" in str(llm.calls[0][0])
    assert len(llm.calls[0][0]) == 2
    assert "task_reminder" in llm.calls[0][0][0]["content"]


def test_explicit_idea_prefix_routes_to_capture_without_llm():
    llm = FakeLLM({})

    result = asyncio.run(
        Router(llm, model="router-model").route(
            context("记录一个想法：我想做一个自媒体博主，关于ai方向的")
        )
    )

    assert result.intent is Intent.CAPTURE
    assert result.capture is not None
    assert result.capture.title == "我想做一个自媒体博主，关于ai方向的"
    assert result.capture.capture_type == "idea"
    assert result.ambiguities == []
    assert llm.calls == []


def test_low_confidence_is_never_executable():
    gate = ExecutionGate.evaluate(
        decision(confidence="0.64", items=[], reason_code="uncertain"), "可能是午饭35"
    )
    assert gate.execute is False
    assert gate.question


def test_give_person_money_is_ambiguous_even_if_model_is_confident():
    result = decision(
        confidence="0.99",
        items=[
            {
                "operation_index": 0,
                "description": "给小王",
                "amount": "500",
                "currency": "CNY",
                "transaction_type": "EXPENSE",
            }
        ],
    )
    gate = ExecutionGate.evaluate(result, "给小王500")
    assert gate.execute is False
    assert gate.question == "这 500 元是消费、借给对方、还款，还是其他用途？"


@pytest.mark.parametrize("indexes", [[1], [0, 2], [0, 0]])
def test_item_indexes_must_be_unique_and_contiguous(indexes):
    items = [
        {
            "operation_index": index,
            "description": f"item-{position}",
            "amount": "1",
            "currency": "CNY",
            "transaction_type": "EXPENSE",
        }
        for position, index in enumerate(indexes)
    ]
    with pytest.raises(PydanticValidationError):
        decision(items=items)


def test_missing_amount_is_rejected_by_schema():
    with pytest.raises(PydanticValidationError):
        decision(
            items=[
                {
                    "operation_index": 0,
                    "description": "午饭",
                    "currency": "CNY",
                    "transaction_type": "EXPENSE",
                }
            ]
        )


def test_multiple_ambiguities_produce_one_question():
    gate = ExecutionGate.evaluate(
        decision(ambiguities=["金额不明确", "时间不明确"]), "大概买了东西"
    )
    assert gate.execute is False
    assert gate.question == "金额不明确？"
    assert gate.question.count("？") == 1


def test_unknown_produces_one_minimal_question():
    gate = ExecutionGate.evaluate(
        decision(intent="UNKNOWN", confidence="0.40", items=[], reason_code="unknown"),
        "帮我看看",
    )
    assert gate.execute is False
    assert gate.question.count("？") == 1


def test_correction_shape_is_accepted_without_create_items():
    result = decision(
        items=[],
        correction={
            "operation_index": 0,
            "match_text": "狗粮",
            "old_amount": "280",
            "new_amount": "260",
        },
        reason_code="correction",
    )
    assert result.correction.new_amount == "260"
    assert ExecutionGate.evaluate(result, "刚才狗粮不是280，是260").execute is True


def test_items_and_correction_cannot_coexist():
    with pytest.raises(PydanticValidationError):
        decision(
            correction={
                "operation_index": 0,
                "match_text": "狗粮",
                "old_amount": "280",
                "new_amount": "260",
            }
        )


@pytest.mark.parametrize("confidence", ["NaN", "Infinity", "-0.1", "1.1", "not-a-number"])
def test_confidence_must_be_finite_probability(confidence):
    with pytest.raises(PydanticValidationError):
        decision(confidence=confidence)
