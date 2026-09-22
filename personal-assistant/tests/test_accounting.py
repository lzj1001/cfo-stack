from decimal import Decimal

import pytest

from personal_assistant.accounting import (
    format_accounting_receipt,
    normalize_item,
    to_daily_fields,
)
from personal_assistant.errors import ValidationError
from personal_assistant.router import AccountingItem


def item(description, amount, transaction_type="EXPENSE", index=0, **changes):
    values = {
        "operation_index": index,
        "description": description,
        "amount": amount,
        "currency": "CNY",
        "transaction_type": transaction_type,
    }
    values.update(changes)
    return AccountingItem(**values)


def test_expense_amount_is_integer_fen():
    result = normalize_item("om_1", item("午饭", "35"), raw_text="午饭35")
    assert result.amount_fen == 3500
    assert result.transaction_id == "om_1:0"


def test_credit_card_repayment_overrides_model_expense():
    result = normalize_item(
        "om_2", item("信用卡还款", "3000"), raw_text="信用卡还款3000"
    )
    assert result.transaction_type == "CREDIT_CARD_REPAYMENT"
    assert result.counts_as_expense is False


def test_investment_overrides_model_expense():
    result = normalize_item("om_3", item("买基金", "1000"), raw_text="基金买了1000")
    assert result.transaction_type == "INVESTMENT"
    assert result.counts_as_expense is False


@pytest.mark.parametrize("amount", ["35.001", "NaN", "Infinity", "-1", "0"])
def test_invalid_money_is_rejected(amount):
    with pytest.raises(ValidationError):
        normalize_item("om_4", item("午饭", amount), raw_text="午饭")


def test_two_item_amount_conservation():
    transactions = [
        normalize_item("om_5", item("午饭", "35", index=0), raw_text="午饭35，狗粮280"),
        normalize_item("om_5", item("狗粮", "280", index=1), raw_text="午饭35，狗粮280"),
    ]
    assert sum(transaction.amount_fen for transaction in transactions) == 31500


def test_refund_requires_related_transaction_id():
    with pytest.raises(ValidationError, match="related transaction"):
        normalize_item(
            "om_6", item("狗粮退款", "20", transaction_type="REFUND"), raw_text="狗粮退款20"
        )


def test_daily_fields_preserve_raw_text_and_unknowns():
    transaction = normalize_item(
        "om_7", item("午饭", "35"), raw_text="今天午饭35", confidence="0.97"
    )
    fields = to_daily_fields(transaction, receipt_id="rcpt_7")

    assert fields["金额"] == Decimal("35")
    assert fields["原始口述"] == "今天午饭35"
    assert fields["交易ID"] == "om_7:0"
    assert fields["来源消息ID"] == "om_7"
    assert fields["回执ID"] == "rcpt_7"
    assert fields["真实用途大类"] == "待确认"
    assert "支付渠道提示" not in fields
    assert "对方或商户原文" not in fields


def test_receipt_text_uses_program_values():
    transactions = [
        normalize_item("om_8", item("午饭", "35", index=0), raw_text="午饭35，狗粮280"),
        normalize_item("om_8", item("狗粮", "280", index=1), raw_text="午饭35，狗粮280"),
    ]
    assert format_accounting_receipt(transactions) == (
        "已记 2 笔：\n- 午饭 ¥35 · 待确认\n- 狗粮 ¥280 · 待确认"
    )
