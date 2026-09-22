import pytest
from decimal import Decimal

from personal_assistant.errors import FeishuWriteError, NotFoundError
from personal_assistant.feishu import FeishuAdapter


def test_create_requires_matching_readback():
    calls = []

    def request(method, path, json=None):
        calls.append((method, path, json))
        if method == "POST":
            return {"record_id": "rec_1"}
        return {"record_id": "rec_1", "fields": {"来源消息 ID": "wrong"}}

    adapter = FeishuAdapter(request)
    with pytest.raises(FeishuWriteError, match="readback mismatch") as raised:
        adapter.create_verified(
            "base",
            "table",
            {"来源消息 ID": "om_1"},
            verify_fields=("来源消息 ID",),
        )
    assert raised.value.record_id == "rec_1"
    assert [call[0] for call in calls] == ["POST", "GET"]


def test_create_returns_only_after_verified_readback():
    def request(method, path, json=None):
        if method == "POST":
            return {"record_id": "rec_1"}
        return {"record_id": "rec_1", "fields": {"来源消息 ID": "om_1"}}

    result = FeishuAdapter(request).create_verified(
        "base",
        "table",
        {"来源消息 ID": "om_1"},
        verify_fields=("来源消息 ID",),
    )
    assert result.record_id == "rec_1"
    assert result.verified is True


def test_create_wraps_transport_failure_and_posts_once():
    methods = []

    def request(method, path, json=None):
        methods.append(method)
        raise TimeoutError("network timeout")

    with pytest.raises(FeishuWriteError, match="create failed"):
        FeishuAdapter(request).create_verified(
            "base", "table", {"金额": 35}, verify_fields=("金额",)
        )
    assert methods == ["POST"]


def test_create_rejects_missing_record_id():
    methods = []

    def request(method, path, json=None):
        methods.append(method)
        return {}

    with pytest.raises(FeishuWriteError, match="record_id"):
        FeishuAdapter(request).create_verified(
            "base", "table", {"金额": 35}, verify_fields=("金额",)
        )
    assert methods == ["POST"]


def test_get_record_raises_not_found_for_empty_response():
    adapter = FeishuAdapter(lambda method, path, json=None: {})
    with pytest.raises(NotFoundError):
        adapter.get_record("base", "table", "rec_missing")


def test_list_records_accepts_empty_table_response():
    adapter = FeishuAdapter(
        lambda method, path, json=None: {"total": 0, "has_more": False}
    )

    assert adapter.list_records("base", "empty_table") == []


def test_numeric_zero_is_not_treated_as_empty():
    def request(method, path, json=None):
        if method == "POST":
            return {"record_id": "rec_zero"}
        return {"record_id": "rec_zero", "fields": {"金额": 0}}

    result = FeishuAdapter(request).create_verified(
        "base", "table", {"金额": 0}, verify_fields=("金额",)
    )
    assert result.fields["金额"] == 0


def test_numeric_string_readback_matches_numeric_expected_value():
    def request(method, path, json=None):
        if method == "POST":
            return {"record_id": "rec_number"}
        return {"record_id": "rec_number", "fields": {"来源分项序号": "0"}}

    result = FeishuAdapter(request).create_verified(
        "base",
        "table",
        {"来源分项序号": Decimal("0")},
        verify_fields=("来源分项序号",),
    )

    assert result.record_id == "rec_number"


def test_update_requires_matching_readback():
    methods = []

    def request(method, path, json=None):
        methods.append(method)
        if method == "PUT":
            return {"record_id": "rec_1"}
        return {"record_id": "rec_1", "fields": {"金额": 280}}

    with pytest.raises(FeishuWriteError, match="readback mismatch"):
        FeishuAdapter(request).update_verified(
            "base", "table", "rec_1", {"金额": 260}, verify_fields=("金额",)
        )
    assert methods == ["PUT", "GET"]


def test_create_wraps_readback_transport_failure_without_second_post():
    methods = []

    def request(method, path, json=None):
        methods.append(method)
        if method == "POST":
            return {"record_id": "rec_1"}
        raise TimeoutError("readback timeout")

    with pytest.raises(FeishuWriteError, match="readback failed"):
        FeishuAdapter(request).create_verified(
            "base", "table", {"来源消息 ID": "om_1"}, verify_fields=("来源消息 ID",)
        )
    assert methods == ["POST", "GET"]


def test_schema_mapping_reads_tables_fields_and_links():
    def request(method, path, json=None):
        if path.endswith("/tables"):
            return {"tables": [{"id": "tbl_daily", "name": "日常财务记录"}]}
        return {
            "fields": [
                {"id": "fld_text", "name": "原始口述", "type": "text"},
                {
                    "id": "fld_link",
                    "name": "关联人物",
                    "type": "link",
                    "link_table": "tbl_people",
                    "bidirectional": True,
                    "bidirectional_link_field_id": "fld_peer",
                },
            ]
        }

    adapter = FeishuAdapter(request)
    tables = adapter.list_tables("base")
    fields = adapter.list_fields("base", "tbl_daily")

    assert [(table.table_id, table.name) for table in tables] == [
        ("tbl_daily", "日常财务记录")
    ]
    assert fields[1].link_table == "tbl_people"
    assert fields[1].bidirectional_link_field_id == "fld_peer"
