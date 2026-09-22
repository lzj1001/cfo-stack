from __future__ import annotations

from .errors import LLMUnavailableError, PersonalAssistantError
from .models import AssistantResponse, Intent, MessageContext
from .router import ExecutionGate
from zoneinfo import ZoneInfo


class AssistantApp:
    def __init__(
        self,
        *,
        router: object,
        accounting: object,
        task_reminders: object | None = None,
        captures: object | None = None,
        reviews: object | None = None,
    ) -> None:
        self.router = router
        self.accounting = accounting
        self.task_reminders = task_reminders
        self.captures = captures
        self.reviews = reviews

    async def handle_message(self, ctx: MessageContext) -> AssistantResponse:
        action = _reminder_action(ctx.text)
        if action and self.task_reminders is not None:
            return self.task_reminders.apply_user_action(ctx, action)
        try:
            decision = await self.router.route(ctx)
        except LLMUnavailableError:
            return AssistantResponse(
                text="暂时无法可靠理解这条消息，没有执行任何写入。",
                status="failed",
            )

        gate = ExecutionGate.evaluate(decision, ctx.text)
        if not gate.execute:
            return AssistantResponse(
                text=gate.question or "可以再说明一下你的需求吗？",
                status="needs_clarification",
            )

        if decision.intent is Intent.CHAT:
            return AssistantResponse(text="我在。", status="chat")
        if decision.intent in {Intent.TASK, Intent.REMINDER}:
            if self.task_reminders is None or decision.task_reminder is None:
                return AssistantResponse(
                    text="任务或提醒信息不完整，可以补充时间和事项吗？",
                    status="needs_clarification",
                )
            return self.task_reminders.create(ctx, decision.task_reminder)
        if decision.intent is Intent.CAPTURE:
            if self.captures is None or decision.capture is None:
                return AssistantResponse(
                    text="要保存的内容还不够明确，可以再说具体一点吗？",
                    status="needs_clarification",
                )
            return self.captures.capture(ctx, decision.capture, confidence=decision.confidence)
        if decision.intent is Intent.REVIEW:
            if self.reviews is None:
                return AssistantResponse(
                    text="复盘汇总功能尚未连接。", status="needs_clarification"
                )
            local_date = ctx.received_at.astimezone(ZoneInfo(ctx.timezone)).date().isoformat()
            return await self.reviews.generate(local_date)
        if decision.intent is not Intent.ACCOUNTING:
            return AssistantResponse(
                text="这个功能会在后续阶段接入，目前没有执行写入。",
                status="needs_clarification",
            )

        try:
            if decision.correction is not None:
                return self.accounting.correct(ctx, decision.correction)
            return self.accounting.record(ctx, decision)
        except PersonalAssistantError:
            return AssistantResponse(
                text="这笔记录暂时没有写入成功。我保留了请求，没有重复记账。",
                status="failed",
            )


def _reminder_action(text: str) -> str | None:
    if "别再催" in text:
        return "cancel"
    if "晚点提醒" in text:
        return "snooze"
    if "今天不做" in text:
        return "skip"
    if text.strip() in {"完成", "完成了", "做完了"}:
        return "complete"
    return None
