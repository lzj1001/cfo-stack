import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from personal_assistant.app import AssistantApp
from personal_assistant.models import AssistantResponse, MessageContext
from personal_assistant.router import RouterDecision


def context(text, message_id="om_task"):
    return MessageContext(
        message_id=message_id,
        user_id="ou_1",
        text=text,
        received_at=datetime(2026, 9, 22, 18, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def reminder_decision():
    return RouterDecision.model_validate(
        {
            "schema_version": "router-v1",
            "intent": "REMINDER",
            "confidence": "0.97",
            "items": [],
            "correction": None,
            "task_reminder": {
                "operation_index": 0,
                "title": "洗衣服",
                "date_reference": "tomorrow",
                "daypart": "evening",
            },
            "ambiguities": [],
            "reason_code": "explicit_reminder",
        }
    )


class FakeRouter:
    async def route(self, ctx):
        return reminder_decision()


class FakeAccounting:
    def record(self, ctx, decision):
        raise AssertionError("accounting must not run")


class FakeTaskReminders:
    def __init__(self):
        self.created = 0
        self.actions = []

    def create(self, ctx, request):
        self.created += 1
        return AssistantResponse(text="已创建任务并安排提醒", status="completed", receipt_id="r1")

    def apply_user_action(self, ctx, action):
        self.actions.append(action)
        return AssistantResponse(text=f"action:{action}", status="completed", receipt_id="r2")


def app(service):
    return AssistantApp(
        router=FakeRouter(), accounting=FakeAccounting(), task_reminders=service
    )


def test_reminder_route_creates_task_and_reminder():
    service = FakeTaskReminders()
    response = asyncio.run(app(service).handle_message(context("明天晚上提醒我洗衣服")))
    assert response.status == "completed"
    assert service.created == 1


def test_deterministic_user_actions_bypass_router_state_choice():
    for text, action in [
        ("完成了", "complete"),
        ("晚点提醒", "snooze"),
        ("今天不做", "skip"),
        ("别再催洗衣服", "cancel"),
    ]:
        service = FakeTaskReminders()
        response = asyncio.run(app(service).handle_message(context(text, f"om_{action}")))
        assert response.status == "completed"
        assert service.actions == [action]
        assert service.created == 0
