from __future__ import annotations

import asyncio
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from .accounting_service import AccountingService, RemoteAuditSink
from .app import AssistantApp
from .capture_service import CaptureService
from .config import Settings
from .errors import PersonalAssistantError, ValidationError
from .feishu import FeishuAdapter
from .feishu_runtime import FeishuOpenAPITransport
from .llm import RequestsTransport, SiliconFlowClient
from .models import AssistantResponse, MessageContext
from .review_service import ReviewService
from .router import Router
from .state import StateStore
from .task_service import TaskReminderService
from .dispatcher import ReminderDispatcher


def context_from_payload(
    payload: Mapping[str, object],
    env: Mapping[str, str],
    *,
    now: Callable[[], datetime] | None = None,
) -> MessageContext:
    message_id = str(env.get("HERMES_SESSION_MESSAGE_ID", "")).strip()
    user_id = str(env.get("HERMES_SESSION_USER_ID", "")).strip()
    text = str(payload.get("text", ""))
    if not message_id:
        raise ValidationError("Hermes session message ID is required")
    if not user_id:
        raise ValidationError("Hermes session user ID is required")
    timezone = str(env.get("PA_TIMEZONE", "Asia/Shanghai"))
    clock = now or (lambda: datetime.now(ZoneInfo(timezone)))
    return MessageContext(
        message_id=message_id,
        user_id=user_id,
        text=text,
        received_at=clock(),
        timezone=timezone,
    )


def build_app(settings: Settings, env: Mapping[str, str]) -> tuple[AssistantApp, StateStore]:
    if not settings.audit_table_id:
        raise ValidationError("PA_AUDIT_TABLE_ID is required")
    transport = FeishuOpenAPITransport(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
    )
    adapter = FeishuAdapter(transport)
    state_path = Path(
        env.get("PA_STATE_DB")
        or Path(env.get("HERMES_HOME") or Path.home() / ".hermes")
        / "state"
        / "personal-assistant.db"
    )
    store = StateStore(state_path)
    store.migrate()
    llm = SiliconFlowClient(
        post=RequestsTransport(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout_seconds=settings.timeout_seconds,
        ),
        max_retries=settings.max_retries,
        metadata_sink=store.record_model_call,
    )

    def audit_factory(**kwargs):
        ctx = kwargs.get("ctx")
        decision = kwargs.get("decision")
        kind = str(kwargs.get("kind") or "accounting")
        intent = (
            decision.intent.value
            if decision is not None
            else {
                "task": "TASK",
                "reminder": "REMINDER",
                "capture": "CAPTURE",
                "review": "REVIEW",
            }.get(kind, "ACCOUNTING")
        )
        confidence = decision.confidence if decision is not None else "1"
        raw_input = ctx.text if ctx is not None else str(kwargs.get("target_date") or "")
        model_name = str(
            kwargs.get("model_name")
            or (settings.summary_model if kind == "review" else settings.router_model)
        )
        return RemoteAuditSink(
            adapter=adapter,
            base_token=settings.base_token,
            audit_table_id=settings.audit_table_id,
            context={
                "raw_input": raw_input,
                "intent": intent,
                "model_name": model_name,
                "model_confidence": confidence,
                "action": str(kwargs.get("action") or "create"),
            },
        )

    accounting = AccountingService(
        store=store,
        daily=adapter,
        base_token=settings.base_token,
        daily_table_id=settings.daily_table_id,
        model_name=settings.router_model,
        audit_sink_factory=audit_factory,
    )
    task_reminders = TaskReminderService(
        store=store,
        tasks=adapter,
        reminders=adapter,
        base_token=settings.base_token,
        tasks_table_id=settings.tasks_table_id,
        reminders_table_id=settings.reminders_table_id,
        max_follow_ups=settings.max_follow_ups,
        audit_sink_factory=audit_factory,
    )
    captures = CaptureService(
        store=store,
        adapter=adapter,
        base_token=settings.base_token,
        table_id=settings.captures_table_id,
        audit_sink_factory=audit_factory,
    )
    reviews = ReviewService(
        store=store,
        transactions=adapter,
        tasks=adapter,
        captures=adapter,
        reminders=adapter,
        reviews=adapter,
        base_token=settings.base_token,
        table_ids={
            "transactions": settings.daily_table_id,
            "tasks": settings.tasks_table_id,
            "captures": settings.captures_table_id,
            "reminders": settings.reminders_table_id,
            "reviews": settings.reviews_table_id,
        },
        llm=llm,
        summary_model=settings.summary_model,
        audit_sink_factory=audit_factory,
    )
    return (
        AssistantApp(
            router=Router(llm, model=settings.router_model),
            accounting=accounting,
            task_reminders=task_reminders,
            captures=captures,
            reviews=reviews,
        ),
        store,
    )


def dispatch_due(env: Mapping[str, str]) -> int:
    settings = Settings.from_env(env)
    transport = FeishuOpenAPITransport(
        app_id=settings.feishu_app_id, app_secret=settings.feishu_app_secret
    )
    adapter = FeishuAdapter(transport)
    store = StateStore(Path(env["PA_STATE_DB"]))
    store.migrate()

    def task_title(task_id: str) -> str:
        matches = [
            record
            for record in adapter.list_records(settings.base_token, settings.tasks_table_id)
            if str(record["fields"].get("task_id") or "") == task_id
        ]
        if len(matches) != 1:
            raise ValidationError("task title lookup is ambiguous")
        return str(matches[0]["fields"].get("title") or "待办事项")

    def update_remote(reminder_id: str, fields: dict[str, object]) -> None:
        matches = [
            record
            for record in adapter.list_records(
                settings.base_token, settings.reminders_table_id
            )
            if str(record["fields"].get("reminder_id") or "") == reminder_id
        ]
        if len(matches) != 1:
            raise ValidationError("reminder lookup is ambiguous")
        adapter.update_verified(
            settings.base_token,
            settings.reminders_table_id,
            matches[0]["record_id"],
            fields,
            verify_fields=tuple(fields),
        )

    def send_dm(user_id: str, text: str) -> bool:
        transport(
            "POST",
            "/im/v1/messages?receive_id_type=open_id",
            json={
                "receive_id": user_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )
        return True

    dispatcher = ReminderDispatcher(
        store=store,
        send_dm=send_dm,
        task_title=task_title,
        update_remote=update_remote,
    )
    try:
        return dispatcher.run_due(datetime.now(ZoneInfo(settings.timezone.key)))
    finally:
        store.close()


async def generate_daily_review(env: Mapping[str, str], target_date: str | None = None) -> AssistantResponse:
    settings = Settings.from_env(env)
    app, store = build_app(settings, env)
    try:
        date_value = target_date or datetime.now(settings.timezone).date().isoformat()
        return await app.reviews.generate(date_value)
    finally:
        store.close()


async def run(payload: dict, env: Mapping[str, str]) -> AssistantResponse:
    settings = Settings.from_env(env)
    ctx = context_from_payload(payload, env)
    app, store = build_app(settings, env)
    try:
        return await app.handle_message(ctx)
    finally:
        store.close()


def load_env_file(path: Path, env: dict[str, str]) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        env.setdefault(name.strip(), value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--daily-review", action="store_true")
    parser.add_argument("--date")
    args = parser.parse_args()
    env = dict(os.environ)
    hermes_home = Path(env.get("HERMES_HOME") or Path.home() / ".hermes")
    load_env_file(hermes_home / ".env", env)
    try:
        if args.dispatch:
            count = dispatch_due(env)
            if count:
                print(json.dumps({"sent": count}, separators=(",", ":")))
            return 0
        if args.daily_review:
            response = asyncio.run(generate_daily_review(env, args.date))
            print(response.text)
            return 0 if response.status == "completed" else 2
        payload = json.loads(sys.stdin.read())
        response = asyncio.run(run(payload, env))
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")))
        return 0 if response.status in {"completed", "needs_clarification", "chat"} else 2
    except (ValueError, PersonalAssistantError) as error:
        print(json.dumps(
            AssistantResponse(
                text="请求未执行，且没有产生重复写入。",
                status="failed",
            ).model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
        ))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
