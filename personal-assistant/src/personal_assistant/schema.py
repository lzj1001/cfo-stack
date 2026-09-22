from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError


@dataclass(frozen=True)
class FieldSpec:
    table_name: str
    name: str
    field_type: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableSpec:
    name: str
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class SelectUpdate:
    table_name: str
    field_name: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class SchemaOperation:
    kind: str
    value: TableSpec | FieldSpec | SelectUpdate


@dataclass(frozen=True)
class SchemaPlan:
    create_tables: tuple[TableSpec, ...] = ()
    create_fields: tuple[FieldSpec, ...] = ()
    update_selects: tuple[SelectUpdate, ...] = ()
    delete_fields: tuple[()] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.create_tables or self.create_fields or self.update_selects)

    @property
    def operations(self) -> tuple[SchemaOperation, ...]:
        return (
            *(SchemaOperation("create_table", value) for value in self.create_tables),
            *(SchemaOperation("create_field", value) for value in self.create_fields),
            *(SchemaOperation("update_select", value) for value in self.update_selects),
        )


_DAILY_FIELDS = (
    FieldSpec("日常财务记录", "交易ID", "text"),
    FieldSpec("日常财务记录", "交易类型", "select", ("支出", "收入", "退款", "转账", "信用卡还款", "投资", "借入", "借出", "其他")),
    FieldSpec("日常财务记录", "币种", "text"),
    FieldSpec("日常财务记录", "来源消息ID", "text"),
    FieldSpec("日常财务记录", "来源分项序号", "number"),
    FieldSpec("日常财务记录", "模型置信度", "number"),
    FieldSpec("日常财务记录", "关联原交易ID", "text"),
    FieldSpec("日常财务记录", "回执ID", "text"),
    FieldSpec("日常财务记录", "来源", "text"),
)

_AUDIT_FIELDS = (
    FieldSpec("Audit", "回执ID", "text"),
    FieldSpec("Audit", "消息ID", "text"),
    FieldSpec("Audit", "来源分项序号", "number"),
    FieldSpec("Audit", "原始输入", "text"),
    FieldSpec("Audit", "意图", "select", ("ACCOUNTING", "TASK", "REMINDER", "CAPTURE", "REVIEW", "CHAT", "UNKNOWN")),
    FieldSpec("Audit", "模型名称", "text"),
    FieldSpec("Audit", "模型置信度", "number"),
    FieldSpec("Audit", "操作", "select", ("create", "update")),
    FieldSpec("Audit", "目标ID", "text"),
    FieldSpec("Audit", "结果", "select", ("succeeded", "failed", "audit_pending")),
    FieldSpec("Audit", "错误代码", "text"),
    FieldSpec("Audit", "创建时间", "created_at"),
)


def build_schema_plan(snapshot: dict) -> SchemaPlan:
    tables = snapshot.get("tables") or {}
    daily = tables.get("日常财务记录")
    if not isinstance(daily, dict):
        raise ValidationError("日常财务记录 table is missing")

    create_tables: list[TableSpec] = []
    create_fields: list[FieldSpec] = []
    update_selects: list[SelectUpdate] = []
    daily_fields = daily.get("fields") or {}
    for desired in _DAILY_FIELDS:
        _plan_field(desired, daily_fields, create_fields)

    funding = daily_fields.get("资金性质")
    if not isinstance(funding, dict) or funding.get("type") != "select":
        raise ValidationError("资金性质 field must remain select")
    options = tuple(funding.get("options") or ())
    if "投资" not in options:
        update_selects.append(
            SelectUpdate("日常财务记录", "资金性质", (*options, "投资"))
        )

    audit = tables.get("Audit")
    if audit is None:
        create_tables.append(TableSpec("Audit", _AUDIT_FIELDS))
    elif isinstance(audit, dict):
        audit_fields = audit.get("fields") or {}
        for desired in _AUDIT_FIELDS:
            _plan_field(desired, audit_fields, create_fields)
    else:
        raise ValidationError("Audit table shape is invalid")

    return SchemaPlan(
        create_tables=tuple(create_tables),
        create_fields=tuple(create_fields),
        update_selects=tuple(update_selects),
    )


def apply_schema_plan(writer: object, plan: SchemaPlan) -> SchemaPlan:
    for operation in plan.operations:
        writer.apply(operation)
    remaining = build_schema_plan(writer.snapshot())
    if not remaining.is_empty:
        raise ValidationError("schema readback did not match target")
    return remaining


def _plan_field(
    desired: FieldSpec, existing_fields: dict, create_fields: list[FieldSpec]
) -> None:
    existing = existing_fields.get(desired.name)
    if existing is None:
        create_fields.append(desired)
        return
    if not isinstance(existing, dict) or existing.get("type") != desired.field_type:
        raise ValidationError(f"field type conflict: {desired.name}")
