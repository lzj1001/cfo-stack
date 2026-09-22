from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .errors import ValidationError
from .models import operation_key
from .router import AccountingItem


_TYPE_LABELS = {
    "EXPENSE": "支出",
    "INCOME": "收入",
    "REFUND": "退款",
    "TRANSFER": "转账",
    "CREDIT_CARD_REPAYMENT": "信用卡还款",
    "INVESTMENT": "投资",
    "BORROW": "借入",
    "LEND": "借出",
    "OTHER": "其他",
}

_BASE_CLASSIFICATION = {
    "EXPENSE": ("支出", "消费"),
    "INCOME": ("收入", "收入"),
    "REFUND": ("不计收支", "退款"),
    "TRANSFER": ("不计收支", "转账"),
    "CREDIT_CARD_REPAYMENT": ("不计收支", "转账"),
    "INVESTMENT": ("不计收支", "投资"),
    "BORROW": ("不计收支", "借还款"),
    "LEND": ("不计收支", "借还款"),
    "OTHER": ("待判断", "待确认"),
}


@dataclass(frozen=True)
class NormalizedTransaction:
    transaction_id: str
    source_message_id: str
    operation_index: int
    description: str
    amount_fen: int
    currency: str
    transaction_type: str
    raw_text: str
    confidence: Decimal
    merchant: str | None = None
    category: str | None = None
    subcategory: str | None = None
    payment_account: str | None = None
    related_transaction_id: str | None = None

    @property
    def counts_as_expense(self) -> bool:
        return self.transaction_type == "EXPENSE"


def normalize_item(
    message_id: str,
    item: AccountingItem,
    *,
    raw_text: str,
    confidence: str = "1.0",
) -> NormalizedTransaction:
    if item.currency != "CNY":
        raise ValidationError("currency must be CNY")
    amount = _money(item.amount)
    normalized_type = _deterministic_type(item.description, item.transaction_type)
    if normalized_type == "REFUND" and not item.related_transaction_id:
        raise ValidationError("refund requires related transaction")
    if normalized_type in {"BORROW", "LEND"} and not any(
        word in item.description for word in ("借", "还")
    ):
        raise ValidationError("loan purpose is not explicit")
    if normalized_type == "OTHER":
        raise ValidationError("other transaction purpose is not explicit")
    try:
        confidence_value = Decimal(confidence)
    except InvalidOperation as error:
        raise ValidationError("confidence is invalid") from error
    if not confidence_value.is_finite() or not Decimal("0") <= confidence_value <= Decimal("1"):
        raise ValidationError("confidence is invalid")

    return NormalizedTransaction(
        transaction_id=operation_key(message_id, item.operation_index),
        source_message_id=message_id,
        operation_index=item.operation_index,
        description=item.description,
        amount_fen=int(amount * 100),
        currency=item.currency,
        transaction_type=normalized_type,
        raw_text=raw_text,
        confidence=confidence_value,
        merchant=_clean(item.merchant),
        category=_clean(item.category),
        subcategory=_clean(item.subcategory),
        payment_account=_clean(item.payment_account),
        related_transaction_id=_clean(item.related_transaction_id),
    )


def to_daily_fields(
    transaction: NormalizedTransaction, *, receipt_id: str
) -> dict[str, object]:
    direction, funding_nature = _BASE_CLASSIFICATION[transaction.transaction_type]
    fields: dict[str, object] = {
        "金额": Decimal(transaction.amount_fen) / 100,
        "金额准确性": "准确",
        "收支方向": direction,
        "资金性质": funding_nature,
        "真实用途大类": transaction.category or "待确认",
        "原始口述": transaction.raw_text,
        "信息性质": "用户确认",
        "匹配状态": "待账单",
        "交易ID": transaction.transaction_id,
        "交易类型": _TYPE_LABELS[transaction.transaction_type],
        "币种": transaction.currency,
        "来源消息ID": transaction.source_message_id,
        "来源分项序号": transaction.operation_index,
        "模型置信度": transaction.confidence,
        "回执ID": receipt_id,
        "来源": "feishu_dm",
    }
    optional = {
        "真实用途小类": transaction.subcategory,
        "对方或商户原文": transaction.merchant,
        "支付渠道提示": transaction.payment_account,
        "关联原交易ID": transaction.related_transaction_id,
    }
    fields.update({name: value for name, value in optional.items() if value is not None})
    return fields


def format_accounting_receipt(transactions: list[NormalizedTransaction]) -> str:
    lines = [f"已记 {len(transactions)} 笔："]
    for transaction in transactions:
        lines.append(
            f"- {transaction.description} ¥{_format_yuan(transaction.amount_fen)} · "
            f"{transaction.category or '待确认'}"
        )
    return "\n".join(lines)


def _money(raw: str) -> Decimal:
    try:
        amount = Decimal(raw)
    except InvalidOperation as error:
        raise ValidationError("amount is invalid") from error
    if not amount.is_finite() or amount <= 0:
        raise ValidationError("amount must be positive and finite")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValidationError("amount has more than two decimal places")
    return amount


def _deterministic_type(description: str, proposed: str) -> str:
    if any(word in description for word in ("信用卡还款", "还信用卡")):
        return "CREDIT_CARD_REPAYMENT"
    if any(word in description for word in ("退款", "退回")):
        return "REFUND"
    if any(word in description for word in ("基金", "股票", "理财", "投资")):
        return "INVESTMENT"
    if "互转" in description or ("转" in description and "账户" in description):
        return "TRANSFER"
    return proposed


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _format_yuan(amount_fen: int) -> str:
    text = format(Decimal(amount_fen) / 100, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
