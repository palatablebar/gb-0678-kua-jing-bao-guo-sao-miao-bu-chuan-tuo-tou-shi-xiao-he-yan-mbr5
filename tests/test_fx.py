"""汇率裁决：合同基准日锁定、结算日申报、缺汇率整批失败。"""
import pytest
from decimal import Decimal
from app.fx import normalize_rate_table, resolve_rate, to_rmb, BUILTIN_RATES
from app.errors import DomainError
from app.modules.m6_compensation.rules import evaluate


def _row(parcel="1001", reported="1000", currency="JPY", settlement="2026-11-06",
         contract="2026-11-04", clock_end="2026-11-04T00:00:00+00:00", expected="0.00"):
    return {"parcel": parcel, "_sources": [1], "expected": expected, "reported": reported,
            "reported_currency": currency, "settlement_date": settlement,
            "contract_rate_date": contract, "clock_end": clock_end}


def test_default_table_covers_builtin_pairs():
    table = normalize_rate_table(None)
    assert table == BUILTIN_RATES
    assert resolve_rate(table, "JPY", "2026-11-04") == Decimal("0.0478")


def test_cny_uses_unit_rate_without_table_entry():
    assert resolve_rate(normalize_rate_table({}), "CNY", "2026-11-04") == Decimal("1")


def test_supplied_table_is_authoritative_not_merged():
    table = normalize_rate_table({"JPY": {"2026-11-04": "0.0500"}})
    assert ("JPY", "2026-11-06") not in table  # 内置条目不得泄漏进权威表
    with pytest.raises(DomainError) as e:
        resolve_rate(table, "JPY", "2026-11-06")
    assert e.value.code == "RATE_NOT_FOUND"


def test_bad_rate_inputs_rejected():
    with pytest.raises(DomainError) as e:
        normalize_rate_table({"JPY": {"2026-11-04": "0"}})
    assert e.value.code == "BAD_RATE"
    with pytest.raises(DomainError) as e:
        normalize_rate_table({"JPY": {"2026-13-40": "0.05"}})
    assert e.value.code == "BAD_RATE_DATE"
    with pytest.raises(DomainError) as e:
        normalize_rate_table({"EUR": {"2026-11-04": "7.5"}})
    assert e.value.code == "UNKNOWN_CURRENCY"


def test_rmb_conversion_quantized():
    assert to_rmb("1000", "0.0478") == "47.80"


def test_fx_gap_is_settlement_minus_contract_view():
    # JPY 1000：结算日 0.0472 -> 47.20；合同基准日 0.0478 -> 47.80；汇差 -0.60
    r = evaluate([_row()], {"parameters": {}, "quarantine": []})["rows"][0]
    assert r["reported_cny"] == "47.20"
    assert r["reported_at_contract"] == "47.80"
    assert r["fx_gap"] == "-0.60"
    # 对账以合同基准日口径为准，汇差不重复计入差额
    assert r["final_reported_cny"] == "47.80"
    assert r["difference"] == "-47.80"
    assert r["status"] == "差异"


def test_contract_rate_date_locks_even_when_settlement_differs():
    r = evaluate([_row(contract="2026-11-04")],
                 {"parameters": {"fx_rates": {
                     "JPY": {"2026-11-04": "0.0478", "2026-11-06": "0.0472"}}},
                  "quarantine": []})["rows"][0]
    assert r["contract_rate"] == "0.0478" and r["settlement_rate"] == "0.0472"


def test_missing_rate_fails_whole_batch_without_partial_rows():
    with pytest.raises(DomainError) as e:
        evaluate([_row()], {"parameters": {"fx_rates": {"JPY": {"2026-11-04": "0.0478"}}},
                 "quarantine": []})
    assert e.value.code == "RATE_NOT_FOUND"


def test_batch_identity_expected_minus_final_reported_equals_difference():
    rows = evaluate([_row("1001"), _row("1002", reported="10", currency="USD")],
                    {"parameters": {}, "quarantine": []})
    total = sum((Decimal(r["difference"]) for r in rows["rows"]), Decimal(0))
    assert total == Decimal(rows["metrics"]["difference_total"])
    assert Decimal(rows["metrics"]["difference_total"]) == \
        Decimal(rows["metrics"]["expected_total"]) - Decimal(rows["metrics"]["reported_total"])
    assert Decimal(rows["metrics"]["fx_gap_total"]) == \
        sum((Decimal(r["fx_gap"]) for r in rows["rows"]), Decimal(0))


def test_adjustment_uses_new_batch_rate_and_keeps_original_amount():
    ctx = {"parameters": {}, "quarantine": [],
           "adjustments": {"1001": [
               {"scan_id": "0124", "batch_date": "2026-11-18", "amount": "800"}]}}
    r = evaluate([_row(reported="500")], ctx)["rows"][0]
    # (800-500) * 0.0469 = 14.07；合同口径原值 23.90 保留，最终 37.97
    assert r["reported_amount"] == "500"  # 原始申报金额未被改写
    assert r["adjustments"][0]["old_amount"] == "500"
    assert r["adjustments"][0]["new_amount"] == "800"
    assert r["adjustments"][0]["batch_rate"] == "0.0469"
    assert r["adjustment_total"] == "14.07"
    assert r["final_reported_cny"] == "37.97"


def test_repeated_adjustment_retry_does_not_double_reverse():
    adj = [{"scan_id": "0124", "batch_date": "2026-11-18", "amount": "800"},
           {"scan_id": "0124", "batch_date": "2026-11-18", "amount": "800"}]
    r = evaluate([_row(reported="500")],
                 {"parameters": {}, "quarantine": [], "adjustments": {"1001": adj}})["rows"][0]
    assert len(r["adjustments"]) == 1
    assert r["adjustment_total"] == "14.07"


def test_no_change_adjustment_creates_no_entry():
    adj = [{"scan_id": "0124", "batch_date": "2026-11-18", "amount": "500"}]
    r = evaluate([_row(reported="500")],
                 {"parameters": {}, "quarantine": [], "adjustments": {"1001": adj}})["rows"][0]
    assert r["adjustments"] == [] and r["adjustment_total"] == "0.00"


def test_second_adjustment_chains_off_previous_new_amount():
    adj = [{"scan_id": "0124", "batch_date": "2026-11-18", "amount": "800"},
           {"scan_id": "0125", "batch_date": "2026-11-06", "amount": "600"}]
    r = evaluate([_row(reported="500")],
                 {"parameters": {}, "quarantine": [], "adjustments": {"1001": adj}})["rows"][0]
    # 第二笔：(600-800) * 0.0472 = -9.44，旧金额取上一笔的新金额
    assert r["adjustments"][1]["old_amount"] == "800"
    assert r["adjustments"][1]["adjustment"] == "-9.44"
    assert r["adjustment_total"] == "4.63"


def test_unknown_carrier_currency_rejected():
    with pytest.raises(DomainError) as e:
        evaluate([_row(currency="EUR")], {"parameters": {}, "quarantine": []})
    assert e.value.code == "UNKNOWN_CURRENCY"


def test_undelivered_parcel_defaults_rate_date_to_clock_end():
    row = {"parcel": "P", "_sources": [1], "expected": "5.00", "reported": "100",
           "reported_currency": "", "settlement_date": "",
           "clock_end": "2026-11-18T00:00:00+00:00"}
    r = evaluate([row], {"parameters": {}, "quarantine": []})["rows"][0]
    assert r["reported_currency"] == "CNY"
    assert r["contract_rate_date"] == "2026-11-18" and r["settlement_date"] == "2026-11-18"
