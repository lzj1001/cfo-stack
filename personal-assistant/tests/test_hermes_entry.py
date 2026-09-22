from datetime import datetime

import pytest

from personal_assistant.errors import ValidationError
from personal_assistant.hermes_entry import context_from_payload


def test_context_uses_real_hermes_session_ids():
    ctx = context_from_payload(
        {"text": "午饭35"},
        {
            "HERMES_SESSION_MESSAGE_ID": "om_real",
            "HERMES_SESSION_USER_ID": "ou_real",
            "PA_TIMEZONE": "Asia/Shanghai",
        },
        now=lambda: datetime.fromisoformat("2026-09-22T12:00:00+08:00"),
    )
    assert ctx.message_id == "om_real"
    assert ctx.user_id == "ou_real"
    assert ctx.text == "午饭35"


def test_missing_message_id_is_rejected_not_replaced_with_random_id():
    with pytest.raises(ValidationError, match="message ID"):
        context_from_payload(
            {"text": "午饭35"},
            {"HERMES_SESSION_USER_ID": "ou_real", "PA_TIMEZONE": "Asia/Shanghai"},
        )
