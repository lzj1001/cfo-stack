import pytest

from personal_assistant.errors import ValidationError
from personal_assistant.schema import apply_schema_plan, build_schema_plan


def target_snapshot():
    daily_fields = {
        name: {"type": field_type}
        for name, field_type in {
            "交易ID": "text",
            "交易类型": "select",
            "币种": "text",
            "来源消息ID": "text",
            "来源分项序号": "number",
            "模型置信度": "number",
            "关联原交易ID": "text",
            "回执ID": "text",
            "来源": "text",
        }.items()
    }
    daily_fields["资金性质"] = {
        "type": "select",
        "options": ["消费", "转账", "投资"],
    }
    audit_fields = {
        name: {"type": field_type}
        for name, field_type in {
            "回执ID": "text",
            "消息ID": "text",
            "来源分项序号": "number",
            "原始输入": "text",
            "意图": "select",
            "模型名称": "text",
            "模型置信度": "number",
            "操作": "select",
            "目标ID": "text",
            "结果": "select",
            "错误代码": "text",
            "创建时间": "created_at",
        }.items()
    }
    return {
        "tables": {
            "日常财务记录": {"id": "daily", "fields": daily_fields},
            "Audit": {"id": "audit", "fields": audit_fields},
        }
    }


def test_schema_plan_adds_only_missing_fields_and_audit():
    snapshot = {
        "tables": {
            "日常财务记录": {
                "id": "daily",
                "fields": {
                    "金额": {"type": "number"},
                    "资金性质": {"type": "select", "options": ["消费", "转账"]},
                },
            }
        }
    }
    plan = build_schema_plan(snapshot)
    assert plan.create_tables[0].name == "Audit"
    assert "交易ID" in {field.name for field in plan.create_fields}
    assert plan.update_selects[0].options == ("消费", "转账", "投资")
    assert plan.delete_fields == ()


def test_schema_plan_is_empty_after_target_schema_exists():
    assert build_schema_plan(target_snapshot()).is_empty


def test_schema_plan_rejects_same_name_type_conflict():
    snapshot = target_snapshot()
    snapshot["tables"]["日常财务记录"]["fields"]["交易ID"] = {"type": "number"}
    with pytest.raises(ValidationError, match="交易ID"):
        build_schema_plan(snapshot)


def test_apply_is_serial_and_verifies_empty_rerun():
    initial = {
        "tables": {
            "日常财务记录": {
                "id": "daily",
                "fields": {
                    "资金性质": {"type": "select", "options": ["消费", "转账"]}
                },
            }
        }
    }

    class Writer:
        def __init__(self):
            self.snapshot_value = initial
            self.actions = []

        def apply(self, operation):
            self.actions.append(operation.kind)
            self.snapshot_value = target_snapshot()

        def snapshot(self):
            return self.snapshot_value

    writer = Writer()
    result = apply_schema_plan(writer, build_schema_plan(initial))
    assert result.is_empty
    assert writer.actions
    assert "delete" not in writer.actions
