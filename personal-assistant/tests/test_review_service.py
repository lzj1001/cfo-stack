import asyncio
from pathlib import Path

from personal_assistant.feishu import FeishuAdapter
from personal_assistant.review_service import ReviewNarrative, ReviewService
from personal_assistant.state import StateStore


class Source:
    def __init__(self, records):
        self.records = records

    def list_records(self, base_token, table_id):
        return [{"record_id": str(i), "fields": fields} for i, fields in enumerate(self.records)]


class ReviewTransport:
    def __init__(self, records=None):
        self.records = records or {}
        self.posts = 0
        self.puts = 0

    def __call__(self, method, path, json=None):
        if method == "POST":
            self.posts += 1
            record_id = f"review_{self.posts}"
            self.records[record_id] = dict(json["fields"])
            return {"record_id": record_id}
        if method == "PUT":
            self.puts += 1
            record_id = path.rsplit("/", 1)[-1]
            self.records[record_id].update(json["fields"])
            return {"record_id": record_id}
        tail = path.rsplit("/", 1)[-1]
        if tail == "records":
            return {"records": [{"record_id": key, "fields": value} for key, value in self.records.items()]}
        return {"record_id": tail, "fields": self.records[tail]}


class FakeLLM:
    def __init__(self):
        self.messages = []
        self.result = ReviewNarrative(summary="支出主要来自餐饮。", priorities=["完成未完成任务"])

    async def structured(self, messages, schema, *, model):
        self.messages.append(messages)
        return self.result


def build(tmp_path: Path, review_records=None, with_data=True):
    store = StateStore(tmp_path / "state.db")
    store.migrate()
    transport = ReviewTransport(review_records)
    llm = FakeLLM()
    service = ReviewService(
        store=store,
        transactions=Source([{"收支方向": "支出", "资金性质": "消费", "金额": 35, "真实用途大类": "餐饮"}] if with_data else []),
        tasks=Source([{"status": "PLANNED"}] if with_data else []),
        captures=Source([],),
        reminders=Source([],),
        reviews=FeishuAdapter(transport),
        base_token="base",
        table_ids={"transactions": "daily", "tasks": "tasks", "captures": "captures", "reminders": "reminders", "reviews": "reviews"},
        llm=llm,
        summary_model="summary",
        audit_sink_factory=lambda **_: lambda receipt: None,
    )
    return service, transport, llm, store


def test_review_updates_existing_same_day_and_llm_receives_facts(tmp_path: Path):
    day_ms = 1790006400000
    service, transport, llm, store = build(
        tmp_path,
        {"existing": {"日期": day_ms, "今日感受": "有点累", "原始口述": "有点累"}},
    )
    response = asyncio.run(service.generate("2026-09-22"))
    assert response.status == "completed"
    assert transport.posts == 0
    assert transport.puts == 1
    assert "expense_fen" in llm.messages[0][1]["content"]
    assert "完整账本" not in str(llm.messages)
    assert "简体中文" in llm.messages[0][0]["content"]
    assert "今日复盘" in response.text
    assert "今日支出：¥35" in response.text
    assert "待办：完成 0，未确认 1，跳过 0" in response.text
    assert response.text.endswith("要补充今天的复盘吗？")
    store.close()


def test_english_model_output_uses_chinese_fallback(tmp_path: Path):
    service, transport, llm, store = build(tmp_path)
    llm.result = ReviewNarrative(
        summary="Spending was mostly food.", priorities=["Finish the task"]
    )

    response = asyncio.run(service.generate("2026-09-22"))

    record = next(iter(transport.records.values()))
    assert "Spending" not in response.text
    assert "Finish" not in response.text
    assert "今日记录已按真实数据汇总。" in response.text
    assert record["自动总结"] == "今日记录已按真实数据汇总。"
    store.close()


def test_empty_day_uses_deterministic_text_without_llm(tmp_path: Path):
    service, transport, llm, store = build(tmp_path, with_data=False)
    response = asyncio.run(service.generate("2026-09-22"))
    assert response.status == "completed"
    assert llm.messages == []
    record = next(iter(transport.records.values()))
    assert record["自动总结"] == "今天没有可汇总的数据。"
    assert record["今日支出"] == 0
    store.close()
