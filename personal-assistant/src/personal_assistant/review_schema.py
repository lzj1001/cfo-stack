from dataclasses import dataclass

from .errors import ValidationError


FIELDS = (
    ("今日支出", "number"), ("支出类别摘要", "text"),
    ("待办完成数", "number"), ("待办未确认数", "number"), ("待办跳过数", "number"),
    ("闪念数量", "number"), ("闪念主题", "text"), ("提醒完成数", "number"),
    ("自动总结", "text"), ("明日优先关注", "text"),
    ("汇总生成时间", "datetime"), ("回执ID", "text"),
)


@dataclass(frozen=True)
class ReviewSchemaPlan:
    create_fields: tuple[tuple[str, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.create_fields


def build_review_schema_plan(snapshot: dict) -> ReviewSchemaPlan:
    existing = set((snapshot.get("fields") or {}).keys())
    required = {name for name, _ in FIELDS}
    present = existing & required
    if present and present != required:
        raise ValidationError("review machine schema is partially present")
    return ReviewSchemaPlan() if present == required else ReviewSchemaPlan(FIELDS)
