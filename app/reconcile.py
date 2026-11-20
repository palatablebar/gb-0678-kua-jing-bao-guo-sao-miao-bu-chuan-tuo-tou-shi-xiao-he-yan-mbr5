"""赔付对账应用服务：补传调整重算与汇率切换（失败回滚上一可用结论）。

流水线保持纯函数；本层负责：
- 读取只追加的补传台账，按包裹组装成重算上下文（原始扫描与旧金额不修改、不删除）；
- 以新 fx_rates 参数对同一原始输入重建批次，成功才追加结论台账并保存新批次；
- 任一票汇率缺失/参数非法时整批切换失败，保留并返回上一可用结论。
"""
from app import store
from app.errors import DomainError
from app.pipeline import calculate
from app.manifest import digest


def _ledger_map(run_id):
    grouped = {}
    for adj in store.list_adjustments(run_id):
        grouped.setdefault(adj["parcel"], []).append({
            "scan_id": adj["scan_id"], "batch_date": adj["batch_date"],
            "amount": adj["amount"], "source": adj["source"]})
    return grouped


def recalculate(run, fx_params, note=""):
    """用给定汇率参数与当前补传台账重算；失败直接抛错，由 switch_fx 回滚。

    fx_params 为 None 时不写入 fx_rates，M6 回退内置默认表；
    为 dict（含空 dict）时作为权威汇率表整体替换。
    """
    raw = store.get_raw(run["run_id"])
    params = dict(run.get("parameters") or {})
    if fx_params is not None:
        params["fx_rates"] = fx_params
    # 先在内存里试算：任何 DomainError 都不会触碰结论台账或 runs 表
    result = calculate(raw, params, adjustments=_ledger_map(run["run_id"]))
    new_run_id = digest(raw, params)[:20]
    store.commit_conclusion(run["run_id"], run["version"] + 1,
                            {} if fx_params is None else {"fx_rates": fx_params},
                            result, note)
    store.save(new_run_id, result, raw)
    return result, new_run_id


def _fallback(run_id, run, previous, exc):
    if previous is None:
        return {"ok": False, "run_id": run_id, "result": run, "rolled_back": True,
                "previous": None, "error": {"code": exc.code, "message": exc.message}}
    return {"ok": False, "run_id": run_id, "result": previous["payload"],
            "rolled_back": True, "previous": previous,
            "error": {"code": exc.code, "message": exc.message}}


def switch_fx(run_id, fx_params, note="汇率切换"):
    """切换汇率日/汇率表。失败时恢复上一可用结论，不覆盖任何已确认结果。

    fx_params 为 None 表示恢复内置默认表；dict 表示权威替换。
    """
    run = store.get(run_id)
    previous = store.latest_conclusion(run_id)
    if fx_params is not None and not isinstance(fx_params, dict):
        return _fallback(run_id, run, previous,
                         DomainError("BAD_FX_PARAMS", "汇率参数需要对象"))
    try:
        result, new_run_id = recalculate(run, fx_params, note)
    except DomainError as exc:
        return _fallback(run_id, run, previous, exc)
    return {"ok": True, "run_id": new_run_id, "result": result,
            "rolled_back": False, "previous": previous}


def apply_adjustment(run_id, payload):
    """登记补传并重算；旧金额与原始扫描保留在台账历史中，重试不重复冲销。"""
    for key in ("parcel", "scan_id", "batch_date", "amount"):
        if not payload.get(key):
            raise DomainError("BAD_ADJUSTMENT", f"补传缺少字段 {key}")
    run = store.get(run_id)
    parcels = {r["parcel"] for r in run["stages"][4]["rows"]}
    if str(payload["parcel"]) not in parcels:
        raise DomainError("UNKNOWN_PARCEL", "补传包裹不在本批次")
    currency = str(payload.get("currency") or "CNY").upper()
    adj = store.add_adjustment(
        run_id, payload["parcel"], payload["scan_id"], payload["batch_date"],
        payload["amount"], currency, payload.get("source", "补传重算"), run["version"])

    current = store.latest_conclusion(run_id)
    if current and "fx_rates" in current["fx_params"]:
        fx_params = current["fx_params"]["fx_rates"]
    else:
        # 从未切换过：None 表示沿用内置默认汇率表
        fx_params = (run.get("parameters") or {}).get("fx_rates")
    outcome = switch_fx(run_id, fx_params, note="补传重算")
    outcome["adjustment"] = adj
    return outcome
