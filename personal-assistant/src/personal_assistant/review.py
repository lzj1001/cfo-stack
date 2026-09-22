from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DailyFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    date: str
    expense_fen: int
    categories: tuple[tuple[str, int], ...]
    tasks_done: int
    tasks_pending: int
    tasks_skipped: int
    capture_count: int
    capture_topics: tuple[str, ...]
    reminders_completed: int
    journal: dict[str, str]


def aggregate_daily_facts(
    target_date: str,
    *,
    transactions: list[dict],
    tasks: list[dict],
    captures: list[dict],
    reminders: list[dict],
    journal: dict[str, str],
) -> DailyFacts:
    expense_fen = 0
    category_totals: dict[str, int] = {}
    for fields in transactions:
        if fields.get("收支方向") != "支出" or fields.get("资金性质") != "消费":
            continue
        fen = int((Decimal(str(fields.get("金额") or 0)) * 100).quantize(Decimal("1")))
        expense_fen += fen
        category = str(fields.get("真实用途大类") or "待确认")
        category_totals[category] = category_totals.get(category, 0) + fen

    done = sum(fields.get("status") == "DONE" for fields in tasks)
    skipped = sum(fields.get("status") == "SKIPPED" for fields in tasks)
    pending = sum(
        fields.get("status") not in {"DONE", "SKIPPED", "CANCELLED"}
        for fields in tasks
    )
    topics: list[str] = []
    for fields in captures:
        raw = str(fields.get("related_topics") or "")
        for topic in raw.replace(",", "、").split("、"):
            topic = topic.strip()
            if topic and topic not in topics:
                topics.append(topic)
    categories = tuple(
        sorted(category_totals.items(), key=lambda item: (-item[1], item[0]))
    )
    clean_journal = {
        str(name): str(value)
        for name, value in journal.items()
        if value is not None and str(value) != ""
    }
    return DailyFacts(
        date=target_date,
        expense_fen=expense_fen,
        categories=categories,
        tasks_done=done,
        tasks_pending=pending,
        tasks_skipped=skipped,
        capture_count=len(captures),
        capture_topics=tuple(topics),
        reminders_completed=sum(
            fields.get("status") == "COMPLETED" for fields in reminders
        ),
        journal=clean_journal,
    )
