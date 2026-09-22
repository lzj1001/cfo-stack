import pytest

from personal_assistant.errors import ValidationError
from personal_assistant.review_schema import build_review_schema_plan


REQUIRED = {
    "今日支出", "支出类别摘要", "待办完成数", "待办未确认数", "待办跳过数",
    "闪念数量", "闪念主题", "提醒完成数", "自动总结", "明日优先关注",
    "汇总生成时间", "回执ID",
}


def test_missing_review_fields_are_planned():
    plan = build_review_schema_plan({"fields": {"日期": {"type": "datetime"}}})
    assert {field[0] for field in plan.create_fields} == REQUIRED


def test_complete_review_schema_is_empty():
    assert build_review_schema_plan({"fields": {name: {"type": "text"} for name in REQUIRED}}).is_empty


def test_partial_existing_machine_schema_is_rejected():
    with pytest.raises(ValidationError):
        build_review_schema_plan({"fields": {"今日支出": {"type": "text"}}})
