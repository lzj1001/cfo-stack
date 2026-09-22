from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr

from .errors import ValidationError


@dataclass(frozen=True)
class Settings:
    api_key: SecretStr
    base_url: str
    router_model: str
    summary_model: str
    timeout_seconds: int
    max_retries: int
    timezone: ZoneInfo
    daily_review_time: time
    max_follow_ups: int
    feishu_app_id: str
    feishu_app_secret: SecretStr
    base_token: str
    daily_table_id: str
    audit_table_id: str | None
    tasks_table_id: str
    reminders_table_id: str
    captures_table_id: str
    reviews_table_id: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        api_key = _required(env, "SILICONFLOW_API_KEY")
        base_url = _required(env, "SILICONFLOW_BASE_URL")
        if not base_url.startswith(("https://", "http://")):
            raise ValidationError("SILICONFLOW_BASE_URL is invalid")

        try:
            timezone = ZoneInfo(_required(env, "PA_TIMEZONE"))
        except ZoneInfoNotFoundError as error:
            raise ValidationError("PA_TIMEZONE is invalid") from error

        try:
            review_time = time.fromisoformat(_required(env, "PA_DAILY_REVIEW_TIME"))
        except ValueError as error:
            raise ValidationError("PA_DAILY_REVIEW_TIME is invalid") from error

        return cls(
            api_key=SecretStr(api_key),
            base_url=base_url.rstrip("/"),
            router_model=_required(env, "PA_ROUTER_MODEL"),
            summary_model=_required(env, "PA_SUMMARY_MODEL"),
            timeout_seconds=_integer(env, "PA_TIMEOUT_SECONDS", minimum=1),
            max_retries=_integer(env, "PA_MAX_RETRIES", minimum=0),
            timezone=timezone,
            daily_review_time=review_time,
            max_follow_ups=_integer(env, "PA_MAX_FOLLOW_UPS", minimum=0),
            feishu_app_id=_required(env, "FEISHU_APP_ID"),
            feishu_app_secret=SecretStr(_required(env, "FEISHU_APP_SECRET")),
            base_token=_required(env, "PA_BASE_TOKEN"),
            daily_table_id=_required(env, "PA_DAILY_TABLE_ID"),
            audit_table_id=_optional(env, "PA_AUDIT_TABLE_ID"),
            tasks_table_id=_required(env, "PA_TASKS_TABLE_ID"),
            reminders_table_id=_required(env, "PA_REMINDERS_TABLE_ID"),
            captures_table_id=_required(env, "PA_CAPTURES_TABLE_ID"),
            reviews_table_id=_required(env, "PA_REVIEWS_TABLE_ID"),
        )


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name, "")).strip()
    if not value:
        raise ValidationError(f"{name} is required")
    return value


def _integer(env: Mapping[str, str], name: str, *, minimum: int) -> int:
    raw = _required(env, name)
    try:
        value = int(raw)
    except ValueError as error:
        raise ValidationError(f"{name} must be an integer") from error
    if value < minimum:
        raise ValidationError(f"{name} must be at least {minimum}")
    return value


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = str(env.get(name, "")).strip()
    return value or None
