import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from personal_assistant.app import AssistantApp
from personal_assistant.models import AssistantResponse, MessageContext
from personal_assistant.router import RouterDecision


class Router:
    async def route(self, ctx):
        return RouterDecision.model_validate({
            "schema_version":"router-v1","intent":"REVIEW","confidence":"0.96",
            "items":[],"correction":None,"task_reminder":None,"capture":None,
            "ambiguities":[],"reason_code":"explicit_review"
        })


class Reviews:
    def __init__(self): self.dates=[]
    async def generate(self, date):
        self.dates.append(date)
        return AssistantResponse(text="今日总结",status="completed",receipt_id="r")


def test_review_route_uses_local_date():
    reviews=Reviews()
    app=AssistantApp(router=Router(),accounting=object(),reviews=reviews)
    ctx=MessageContext(message_id="om_r",user_id="u",text="今日复盘汇总",received_at=datetime(2026,9,22,23,tzinfo=ZoneInfo("Asia/Shanghai")))
    response=asyncio.run(app.handle_message(ctx))
    assert response.status=="completed"
    assert reviews.dates==["2026-09-22"]
