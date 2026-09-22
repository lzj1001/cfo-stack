from personal_assistant.review import aggregate_daily_facts


DAY_MS = 1790006400000


def test_program_aggregates_real_daily_facts():
    facts = aggregate_daily_facts(
        "2026-09-22",
        transactions=[
            {"发生时间": DAY_MS, "收支方向": "支出", "资金性质": "消费", "金额": 35, "真实用途大类": "餐饮与食品"},
            {"发生时间": DAY_MS, "收支方向": "支出", "资金性质": "消费", "金额": 20.5, "真实用途大类": "交通与出行"},
            {"发生时间": DAY_MS, "收支方向": "不计收支", "资金性质": "转账", "金额": 3000, "真实用途大类": "非消费流量"},
        ],
        tasks=[{"status": "DONE"}, {"status": "PLANNED"}, {"status": "SKIPPED"}],
        captures=[{"title": "海外AI账号", "related_topics": "AI、自媒体"}],
        reminders=[{"status": "COMPLETED"}, {"status": "AWAITING_CONFIRMATION"}],
        journal={"今日感受": "有点累", "原始口述": "今天有点累"},
    )
    assert facts.expense_fen == 5550
    assert facts.categories == (("餐饮与食品", 3500), ("交通与出行", 2050))
    assert (facts.tasks_done, facts.tasks_pending, facts.tasks_skipped) == (1, 1, 1)
    assert facts.capture_count == 1
    assert facts.capture_topics == ("AI", "自媒体")
    assert facts.reminders_completed == 1
    assert facts.journal["今日感受"] == "有点累"


def test_empty_day_is_zero_and_contains_no_inference():
    facts = aggregate_daily_facts(
        "2026-09-22",
        transactions=[],
        tasks=[],
        captures=[],
        reminders=[],
        journal={},
    )
    assert facts.expense_fen == 0
    assert facts.capture_count == 0
    assert facts.journal == {}
    assert facts.model_dump()["categories"] == ()
