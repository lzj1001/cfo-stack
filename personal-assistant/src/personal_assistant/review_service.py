from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from .audit import ReceiptService
from .errors import ValidationError
from .models import AssistantResponse, Receipt
from .review import DailyFacts, aggregate_daily_facts
from .state import StateStore


class ReviewNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str
    priorities: list[str] = Field(max_length=3)


class ReviewService:
    def __init__(
        self,
        *,
        store: StateStore,
        transactions: object,
        tasks: object,
        captures: object,
        reminders: object,
        reviews: object,
        base_token: str,
        table_ids: dict[str, str],
        llm: object,
        summary_model: str,
        audit_sink_factory: Callable[..., Callable[[Receipt], None]] | None,
        timezone: str = "Asia/Shanghai",
    ) -> None:
        if audit_sink_factory is None:
            raise ValidationError("audit sink factory is required")
        self._store = store
        self._sources = {
            "transactions": transactions,
            "tasks": tasks,
            "captures": captures,
            "reminders": reminders,
        }
        self._reviews = reviews
        self._base_token = base_token
        self._table_ids = table_ids
        self._llm = llm
        self._summary_model = summary_model
        self._audit_sink_factory = audit_sink_factory
        self._zone = ZoneInfo(timezone)

    async def generate(self, target_date: str) -> AssistantResponse:
        source_fields = {
            name: [
                record["fields"]
                for record in adapter.list_records(
                    self._base_token, self._table_ids[name]
                )
                if _belongs_to_date(record["fields"], target_date, self._zone)
            ]
            for name, adapter in self._sources.items()
        }
        review_records = self._reviews.list_records(
            self._base_token, self._table_ids["reviews"]
        )
        matches = [
            record
            for record in review_records
            if _date_text(record["fields"].get("日期"), self._zone) == target_date
        ]
        if len(matches) > 1:
            raise ValidationError("multiple daily review records for date")
        journal = {}
        if matches:
            journal = {
                name: str(matches[0]["fields"].get(name) or "")
                for name in ("今日进展", "今日感受", "今日发现", "价值证据", "明日最小行动", "原始口述")
                if matches[0]["fields"].get(name)
            }
        facts = aggregate_daily_facts(
            target_date,
            transactions=source_fields["transactions"],
            tasks=source_fields["tasks"],
            captures=source_fields["captures"],
            reminders=source_fields["reminders"],
            journal=journal,
        )
        narrative = await self._narrative(facts)
        narrative = _chinese_narrative(facts, narrative)
        fact_json = facts.model_dump_json()
        message_id = f"daily-review-v2-{target_date}-{hashlib.sha256(fact_json.encode()).hexdigest()[:16]}"
        target_ms = int(
            datetime.combine(date.fromisoformat(target_date), time.min, tzinfo=self._zone).timestamp()
            * 1000
        )
        sink = self._audit_sink_factory(
            target_date=target_date, kind="review", action="update" if matches else "create"
        )
        receipts = ReceiptService(
            store=self._store,
            feishu=self._reviews,
            audit_sink=sink,
            base_token=self._base_token,
            table_id=self._table_ids["reviews"],
            verify_fields=("自动总结", "今日支出", "回执ID"),
        )

        def fields(receipt_id: str) -> dict[str, object]:
            return {
                "复盘标题": f"{target_date} 今日复盘",
                "日期": target_ms,
                "今日支出": Decimal(facts.expense_fen) / 100,
                "支出类别摘要": "、".join(
                    f"{name} ¥{Decimal(fen) / 100:g}" for name, fen in facts.categories
                ),
                "待办完成数": facts.tasks_done,
                "待办未确认数": facts.tasks_pending,
                "待办跳过数": facts.tasks_skipped,
                "闪念数量": facts.capture_count,
                "闪念主题": "、".join(facts.capture_topics),
                "提醒完成数": facts.reminders_completed,
                "自动总结": narrative.summary,
                "明日优先关注": "、".join(narrative.priorities),
                "汇总生成时间": int(datetime.now(self._zone).timestamp() * 1000),
                "回执ID": receipt_id,
            }

        if matches:
            receipt = receipts.execute_update(
                message_id, 0, record_id=matches[0]["record_id"], fields=fields
            )
        else:
            receipt = receipts.execute_create(message_id, 0, fields=fields)
        if receipt.status != "completed":
            return AssistantResponse(
                text="今日复盘暂时没有保存成功。",
                status="failed",
                receipt_id=receipt.receipt_id,
            )
        return AssistantResponse(
            text=_format_review_response(facts, narrative),
            status="completed",
            receipt_id=receipt.receipt_id,
        )

    async def _narrative(self, facts: DailyFacts) -> ReviewNarrative:
        if (
            facts.expense_fen == 0
            and facts.tasks_done == 0
            and facts.tasks_pending == 0
            and facts.tasks_skipped == 0
            and facts.capture_count == 0
            and facts.reminders_completed == 0
            and not facts.journal
        ):
            return ReviewNarrative(summary="今天没有可汇总的数据。", priorities=[])
        return await self._llm.structured(
            [
                {
                    "role": "system",
                    "content": "Use only the supplied facts. Write in Simplified Chinese (简体中文). Do not infer emotion, health, or efficiency. Return summary and at most three priorities.",
                },
                {"role": "user", "content": facts.model_dump_json()},
            ],
            ReviewNarrative,
            model=self._summary_model,
        )


def _belongs_to_date(fields: dict, target_date: str, zone: ZoneInfo) -> bool:
    for name in ("发生时间", "planned_at", "due_at", "captured_at", "scheduled_at", "created_at"):
        if name in fields and fields[name] not in (None, ""):
            return _date_text(fields[name], zone) == target_date
    return True


def _date_text(value: object, zone: ZoneInfo) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, zone).date().isoformat()
    text = str(value or "")
    if text.isdigit() and len(text) >= 12:
        return datetime.fromtimestamp(int(text) / 1000, zone).date().isoformat()
    return text[:10]


def _chinese_narrative(
    facts: DailyFacts, narrative: ReviewNarrative
) -> ReviewNarrative:
    summary = (
        narrative.summary
        if _contains_chinese(narrative.summary)
        else "今日记录已按真实数据汇总。"
    )
    priorities = [item for item in narrative.priorities if _contains_chinese(item)]
    return ReviewNarrative(summary=summary, priorities=priorities)


def _format_review_response(facts: DailyFacts, narrative: ReviewNarrative) -> str:
    expense = Decimal(facts.expense_fen) / 100
    categories = "、".join(
        f"{name} ¥{Decimal(fen) / 100:g}" for name, fen in facts.categories
    ) or "暂无"
    topics = "、".join(facts.capture_topics) or "暂无"
    priorities = "、".join(narrative.priorities) or "暂无"
    return (
        "今日复盘\n"
        f"- 今日支出：¥{expense:g}；主要类别：{categories}\n"
        f"- 待办：完成 {facts.tasks_done}，未确认 {facts.tasks_pending}，跳过 {facts.tasks_skipped}\n"
        f"- 闪念：{facts.capture_count} 条；主题：{topics}\n"
        f"- 提醒完成：{facts.reminders_completed}\n"
        f"- 今日总结：{narrative.summary}\n"
        f"- 明天优先关注：{priorities}\n"
        "要补充今天的复盘吗？"
    )


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)
