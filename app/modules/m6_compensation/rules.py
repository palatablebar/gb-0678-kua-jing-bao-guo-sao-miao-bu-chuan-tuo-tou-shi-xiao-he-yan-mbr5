"""M6 赔付对账：合同基准日锁定汇率，补传按新批次汇率重算。

裁决口径（每票唯一对账结论）：
1. 合同预期赔付 expected 以人民币计赔，锁定合同基准日（默认妥投日，未妥投取 as_of）。
2. 承运商按结算日汇率以外币申报：申报金额原值与原始扫描全部保留，只折算不覆盖。
3. 汇差 = 同一份申报金额在结算日汇率与合同基准日汇率下的人民币差额。
4. 补传差额仅在补传改变金额时产生，按新批次（补传上传日）汇率重算，逐次登记调整；
   同一补传重试（同扫描号、同批次日、同金额）不重复冲销，调整表只追加不改写、不删除。
5. 任一票汇率缺失即整批切换失败，由服务层回滚到上一可用结论，绝不落库半成品。
"""
from decimal import Decimal, ROUND_HALF_UP
from app.reasons import sources
from app.errors import DomainError
from app.values import number, money
from app.fx import normalize_rate_table, resolve_rate, _date_text, KNOWN_CURRENCIES

TWO = Decimal("0.01")


def _day_of(instant_text):
    return str(instant_text)[:10]


def _q2(value):
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def _carrier_inputs(row):
    """从上游行解析承运商申报，缺币种按人民币，缺结算日回退妥投日。"""
    currency = str(row.get("reported_currency") or "CNY").strip().upper()
    if currency not in KNOWN_CURRENCIES:
        raise DomainError("UNKNOWN_CURRENCY", f"包裹 {row.get('parcel')} 申报币种 {currency} 未登记")
    raw_amount = number(row.get("reported", "0"))
    fallback = row.get("settlement_date") or _day_of(row["clock_end"])
    return currency, raw_amount, _date_text(fallback)


def _build_row(row, table, adjustments_seen, parcel_adj):
    currency, raw_amount, settlement = _carrier_inputs(row)
    contract_date = _date_text(row.get("contract_rate_date") or _day_of(row["clock_end"]))

    settlement_rate = resolve_rate(table, currency, settlement)
    contract_rate = resolve_rate(table, currency, contract_date)
    reported_cny = _q2(raw_amount * settlement_rate)
    reported_at_contract = _q2(raw_amount * contract_rate)
    # 汇差：同一申报金额在两个汇率日之间的人民币差（结算口径 − 合同口径）
    fx_gap = _q2(reported_cny - reported_at_contract)

    expected = number(row["expected"])
    base_difference = _q2(expected - reported_at_contract)

    # 补传调整：仅处理显式登记、且确实改变金额的补传，按新批次汇率重算
    ledger = []
    adjustment_total = Decimal(0)
    current_amount = raw_amount
    for adj in parcel_adj:
        token = (str(adj.get("scan_id") or ""), str(adj.get("batch_date") or ""),
                 str(adj.get("amount") or ""))
        if token in adjustments_seen:
            # 同一补传重试：不重复冲销
            continue
        adjustments_seen.add(token)
        new_amount = number(adj["amount"])
        if new_amount == current_amount:
            # 未改变金额，不产生调整分录
            continue
        batch_date = _date_text(adj.get("batch_date") or row.get("delivery_uploaded_at")
                                or contract_date)
        batch_rate = resolve_rate(table, currency, batch_date)
        # 补传差额：新批次汇率下，金额变化对应的人民币重算差
        delta = _q2((new_amount - current_amount) * batch_rate)
        ledger.append({
            "scan_id": str(adj.get("scan_id") or ""),
            "batch_date": batch_date,
            "batch_rate": str(batch_rate),
            "old_amount": str(current_amount),
            "new_amount": str(new_amount),
            "adjustment": str(delta),
            "source": str(adj.get("source") or "补传重算"),
        })
        adjustment_total += delta
        current_amount = new_amount

    final_reported = _q2(reported_at_contract + adjustment_total)
    difference = _q2(expected - final_reported)

    # 恒等式：最终差额 = 基础差额 − 补传差额；
    # 汇差已含在“按合同基准日重述”的口径中（base_difference 用合同口径，reported_cny 仅展示）
    if difference != _q2(base_difference - adjustment_total):
        raise DomainError("RECONCILE_MISMATCH", f"包裹 {row.get('parcel')} 对账恒等式不平")

    return {
        **row,
        "reported_currency": currency,
        "reported_amount": str(raw_amount),
        "settlement_date": settlement,
        "contract_rate_date": contract_date,
        "settlement_rate": str(settlement_rate),
        "contract_rate": str(contract_rate),
        "reported_cny": str(reported_cny),
        "reported_at_contract": str(reported_at_contract),
        "fx_gap": str(fx_gap),
        "adjustments": ledger,
        "adjustment_total": str(_q2(adjustment_total)),
        "final_reported_cny": str(final_reported),
        "difference": str(difference),
        "status": "通过" if difference == 0 else "差异",
    }


def evaluate(rows, context):
    intake = context.get("intake")
    bad = context.get("quarantine", [])
    accepted = set(x for row in rows for x in sources(row))
    rejected = set(x for row in bad for x in sources(row))
    if accepted & rejected:
        raise DomainError("LINEAGE_OVERLAP", "同一来源行被重复裁决")
    m = {"accepted_inputs": len(accepted), "quarantine_inputs": len(rejected),
         "output_records": len(rows)}
    if intake:
        excluded = intake["duplicate"] + intake["blank"] + intake["repeated_headers"]
        m.update(input=intake["input"], excluded=excluded,
                 balanced=intake["input"] == len(accepted) + len(rejected) + excluded)
    else:
        m.update(input=len(accepted) + len(rejected), excluded=0, balanced=True)

    params = context.get("parameters", {})
    # 汇率表构建失败会抛 DomainError：整批不产出，由上层保留上一可用结论
    table = normalize_rate_table(params.get("fx_rates"))
    adjustments = context.get("adjustments", {}) or {}

    seen_tokens = set()
    output = [_build_row(row, table, seen_tokens, adjustments.get(row["parcel"], []))
              for row in rows]

    expected_total = sum((number(r["expected"]) for r in output), Decimal(0))
    reported_total = sum((number(r["final_reported_cny"]) for r in output), Decimal(0))
    fx_total = sum((number(r["fx_gap"]) for r in output), Decimal(0))
    adj_total = sum((number(r["adjustment_total"]) for r in output), Decimal(0))
    diff_total = sum((number(r["difference"]) for r in output), Decimal(0))
    if _q2(diff_total) != _q2(expected_total - reported_total):
        raise DomainError("RECONCILE_MISMATCH", "批次对账合计不平")
    m.update(
        expected_total=money(expected_total),
        reported_total=money(reported_total),
        fx_gap_total=money(fx_total),
        adjustment_total=money(adj_total),
        difference_total=money(diff_total),
        differences=sum(r["status"] == "差异" for r in output),
        fx_rate_dates=sorted(f"{r['reported_currency']}@{r['contract_rate_date']}" for r in output),
    )
    return {"rows": output, "quarantine": [], "metrics": m}
