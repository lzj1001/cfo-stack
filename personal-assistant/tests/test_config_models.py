from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from personal_assistant.config import Settings
from personal_assistant.errors import ValidationError
from personal_assistant.models import MessageContext, operation_key


BASE_ENV = {
    "SILICONFLOW_API_KEY": "secret-for-test",
    "SILICONFLOW_BASE_URL": "https://api.siliconflow.cn/v1",
    "PA_ROUTER_MODEL": "router-model",
    "PA_SUMMARY_MODEL": "summary-model",
    "PA_TIMEOUT_SECONDS": "30",
    "PA_MAX_RETRIES": "2",
    "PA_TIMEZONE": "Asia/Shanghai",
    "PA_DAILY_REVIEW_TIME": "23:30",
    "PA_MAX_FOLLOW_UPS": "1",
    "FEISHU_APP_ID": "cli_test_app",
    "FEISHU_APP_SECRET": "feishu-secret-for-test",
    "PA_BASE_TOKEN": "base_test",
    "PA_DAILY_TABLE_ID": "tbl_daily",
    "PA_AUDIT_TABLE_ID": "",
    "PA_TASKS_TABLE_ID": "tbl_tasks",
    "PA_REMINDERS_TABLE_ID": "tbl_reminders",
    "PA_CAPTURES_TABLE_ID": "tbl_captures",
    "PA_REVIEWS_TABLE_ID": "tbl_reviews",
}


def test_settings_parse_valid_environment():
    settings = Settings.from_env(BASE_ENV)
    assert settings.timezone == ZoneInfo("Asia/Shanghai")
    assert settings.timeout_seconds == 30
    assert settings.api_key.get_secret_value() == "secret-for-test"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SILICONFLOW_API_KEY", ""),
        ("PA_TIMEOUT_SECONDS", "0"),
        ("PA_MAX_RETRIES", "-1"),
        ("PA_TIMEZONE", "Mars/Base"),
    ],
)
def test_settings_reject_invalid_values_without_secret_leak(name, value):
    env = {**BASE_ENV, name: value}
    with pytest.raises(ValidationError) as caught:
        Settings.from_env(env)
    assert "secret-for-test" not in str(caught.value)


def test_message_context_requires_real_message_id_and_aware_time():
    with pytest.raises(ValueError):
        MessageContext(
            message_id="",
            user_id="u1",
            text="午饭35",
            received_at=datetime.now(),
        )


def test_operation_key_is_stable_and_scoped_to_item():
    assert operation_key("om_123", 0) == "om_123:0"
    assert operation_key("om_123", 1) != operation_key("om_123", 0)
