"""汇率表与基准日裁决。

跨境赔付以人民币计赔；承运商按结算日汇率用外币申报。
合同基准日锁定汇率（财务规则），补传改变金额时按新批次汇率重算（运营规则）。
同一货币同一日期只保留一条汇率，重复登记为幂等，冲突登记拒绝且不改动既有汇率。
"""
from decimal import Decimal
from app.values import number, money
from app.errors import DomainError

# 内置汇率表：(币种, 汇率日) -> 1 单位外币兑人民币
BUILTIN_RATES = {
    ("JPY", "2026-11-04"): Decimal("0.0478"),
    ("JPY", "2026-11-06"): Decimal("0.0472"),
    ("JPY", "2026-11-18"): Decimal("0.0469"),
    ("USD", "2026-11-04"): Decimal("7.1000"),
    ("USD", "2026-11-06"): Decimal("7.1200"),
    ("USD", "2026-11-18"): Decimal("7.0800"),
}
KNOWN_CURRENCIES = ("CNY", "JPY", "USD")


def _date_text(value):
    text = str(value).strip()
    # 仅做形态校验，避免把时间戳或脏值当作汇率日
    parts = text.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise DomainError("BAD_RATE_DATE", "汇率日需要 YYYY-MM-DD")
    year, month, day = (int(p) for p in parts)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise DomainError("BAD_RATE_DATE", "汇率日不是合法日期")
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_rate_table(overrides):
    """构建本次计算的权威汇率表。

    - 未提供 fx_rates：使用内置合同汇率表（演示默认）。
    - 提供了 fx_rates（即使为空对象）：以参数为唯一权威，不再回退内置表，
      缺汇率即 RATE_NOT_FOUND，由服务层整批回滚上一可用结论。
    - 同币同日重复给值且不一致：RATE_CONFLICT，拒绝切换。
    """
    if overrides in (None, ""):
        return dict(BUILTIN_RATES)
    if not isinstance(overrides, dict):
        raise DomainError("BAD_RATE_TABLE", "汇率表需要 {币种: {日期: 汇率}} 对象")
    table = {}
    for currency, daily in overrides.items():
        currency = str(currency).strip().upper()
        if currency not in KNOWN_CURRENCIES:
            raise DomainError("UNKNOWN_CURRENCY", f"币种 {currency} 未登记")
        if not isinstance(daily, dict):
            raise DomainError("BAD_RATE_TABLE", "每个币种需要日期到汇率的映射")
        for day, rate in daily.items():
            key = (currency, _date_text(day))
            value = number(rate)
            if value <= 0:
                raise DomainError("BAD_RATE", "汇率必须为正数")
            if key in table and table[key] != value:
                raise DomainError("RATE_CONFLICT", f"{currency} {key[1]} 重复登记且汇率不一致")
            table[key] = value
    return table


def resolve_rate(table, currency, rate_date):
    """取指定币种与汇率日的汇率；缺失即切换失败，交由调用方回滚上一可用结论。"""
    currency = str(currency).strip().upper()
    if currency == "CNY":
        return Decimal("1")
    key = (currency, _date_text(rate_date))
    if key not in table:
        raise DomainError("RATE_NOT_FOUND", f"缺少 {currency} {rate_date} 的汇率")
    return table[key]


def to_rmb(amount, rate):
    """外币金额按锁定汇率折算人民币并保留两位。"""
    return money(number(amount) * number(rate))
