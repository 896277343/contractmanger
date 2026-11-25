"""RMB reporting conversion; keep source and estimated rates distinguishable."""
from decimal import Decimal, ROUND_HALF_UP

STANDARD_RATES = {"人民币": Decimal("1"), "美元": Decimal("7"), "欧元": Decimal("7.5")}
ALIASES = {"R": "人民币", "M": "美元", "U": "欧元", "CNY": "人民币", "RMB": "人民币", "USD": "美元", "EUR": "欧元"}


def normalized_currency(value):
    text = str(value or "").strip()
    return ALIASES.get(text.upper(), text)


def positive_rate(value):
    if value is None:
        return None
    rate = Decimal(str(value))
    return rate if rate > 0 else None


def conversion(amount, currency, contract_rate=None, receipt_rate=None, same_contract_currency=True):
    """Return (rounded RMB amount, rate source, applied rate)."""
    currency = normalized_currency(currency)
    if amount is None:
        return None, "missing_amount", None
    if currency == "人民币":
        return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "native", Decimal("1")
    rate = positive_rate(receipt_rate)
    if rate is None and same_contract_currency:
        rate = positive_rate(contract_rate)
    source = "actual" if rate is not None else "estimated"
    if rate is None:
        rate = STANDARD_RATES.get(currency)
    if rate is None:
        return None, "unknown_currency", None
    result = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return result, source, rate
