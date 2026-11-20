"""补传台账、汇率切换失败回滚、接口级幂等。"""
import pytest
from app.pipeline import execute, load_sample
from app import store, reconcile


@pytest.fixture
def fx_run():
    return execute(load_sample("fx.csv"))


FX_FULL = {"JPY": {"2026-11-04": "0.0478", "2026-11-06": "0.0472", "2026-11-18": "0.0469"},
           "USD": {"2026-11-04": "7.1000", "2026-11-06": "7.1200"}}


def test_adjustment_recalculates_with_new_batch_rate(fx_run):
    out = reconcile.apply_adjustment(
        fx_run["run_id"],
        {"parcel": "1003", "scan_id": "0124", "batch_date": "2026-11-18",
         "amount": "800", "currency": "JPY"})
    assert out["ok"] is True and out["adjustment"]["deduplicated"] is False
    row = [r for r in out["result"]["stages"][5]["rows"] if r["parcel"] == "1003"][0]
    assert row["adjustment_total"] == "14.07" and row["final_reported_cny"] == "37.97"


def test_retry_same_adjustment_is_deduplicated(fx_run):
    payload = {"parcel": "1003", "scan_id": "0124", "batch_date": "2026-11-18",
               "amount": "800", "currency": "JPY"}
    first = reconcile.apply_adjustment(fx_run["run_id"], payload)
    again = reconcile.apply_adjustment(fx_run["run_id"], payload)
    assert again["adjustment"]["deduplicated"] is True
    assert store.list_adjustments(fx_run["run_id"]).__len__() == 1
    row = [r for r in again["result"]["stages"][5]["rows"] if r["parcel"] == "1003"][0]
    assert len(row["adjustments"]) == 1  # 不重复冲销


def test_original_scan_and_old_amount_never_deleted(fx_run):
    rid = fx_run["run_id"]
    reconcile.apply_adjustment(rid, {"parcel": "1003", "scan_id": "0124",
                                     "batch_date": "2026-11-18", "amount": "800",
                                     "currency": "JPY"})
    original = store.get(rid)
    path_row = [r for r in original["stages"][3]["rows"] if r["parcel"] == "1003"][0]
    assert path_row["reported"] == "500"  # 原始申报保留
    assert path_row["_sources"] == [10, 11, 12, 13]
    ledger = store.list_adjustments(rid)
    assert ledger[0]["amount"] == "800" and ledger[0]["scan_id"] == "0124"


def test_failed_switch_rolls_back_to_original_when_no_history(fx_run):
    out = reconcile.switch_fx(fx_run["run_id"], {"JPY": {"2026-11-04": "0.0478"}})
    assert out["ok"] is False and out["rolled_back"] is True
    assert out["error"]["code"] == "RATE_NOT_FOUND"
    assert out["result"]["run_id"] == fx_run["run_id"]
    assert store.conclusions(fx_run["run_id"]) == []  # 失败切换不落结论


def test_failed_switch_rolls_back_to_last_good_conclusion(fx_run):
    rid = fx_run["run_id"]
    good = reconcile.switch_fx(rid, FX_FULL)
    assert good["ok"]
    bad = reconcile.switch_fx(rid, {"JPY": {"2026-11-04": "0.0478"}})
    assert bad["ok"] is False and bad["rolled_back"] is True
    assert bad["result"]["run_id"] == good["run_id"]
    # 只有成功的那次进了结论台账
    assert len(store.conclusions(rid)) == 1


def test_adjustment_unknown_parcel_rejected(fx_run):
    from app.errors import DomainError
    with pytest.raises(DomainError) as e:
        reconcile.apply_adjustment(fx_run["run_id"], {"parcel": "9999", "scan_id": "x",
                                                       "batch_date": "2026-11-18", "amount": "1"})
    assert e.value.code == "UNKNOWN_PARCEL"


def test_raw_bytes_persist_for_recalculation(fx_run):
    assert store.get_raw(fx_run["run_id"]) == load_sample("fx.csv")


def test_adjustment_evidence_survives_rollback_and_recovers(fx_run):
    rid = fx_run["run_id"]
    reconcile.switch_fx(rid, {"JPY": {"2026-11-04": "0.0478", "2026-11-06": "0.0472"},
                              "USD": {"2026-11-04": "7.1000", "2026-11-06": "7.1200"}})
    out = reconcile.apply_adjustment(rid, {"parcel": "1003", "scan_id": "0124",
                                           "batch_date": "2026-11-18", "amount": "800",
                                           "currency": "JPY"})
    assert out["ok"] is False and out["error"]["code"] == "RATE_NOT_FOUND"
    # 台账证据不丢，回滚结论不含该调整
    assert len(store.list_adjustments(rid)) == 1
    assert all(not r["adjustments"] for r in out["result"]["stages"][5]["rows"])
    # 补齐汇率后下一次成功切换自动纳入
    recovered = reconcile.switch_fx(rid, FX_FULL)
    row = [r for r in recovered["result"]["stages"][5]["rows"] if r["parcel"] == "1003"][0]
    assert row["adjustment_total"] == "14.07" and row["final_reported_cny"] == "37.97"


def test_http_adjustment_and_fx_endpoints(client, fx_run):
    rid = fx_run["run_id"]
    r = client.post(f"/api/runs/{rid}/adjustments",
                    json={"parcel": "1003", "scan_id": "0124",
                          "batch_date": "2026-11-18", "amount": "800", "currency": "JPY"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    # 重试幂等
    r2 = client.post(f"/api/runs/{rid}/adjustments",
                     json={"parcel": "1003", "scan_id": "0124",
                           "batch_date": "2026-11-18", "amount": "800", "currency": "JPY"})
    assert r2.get_json()["adjustment"]["deduplicated"] is True
    listed = client.get(f"/api/runs/{rid}/adjustments").get_json()
    assert len(listed["adjustments"]) == 1 and len(listed["conclusions"]) >= 1
    # 切换失败 409 且页面可恢复上一可用结论
    bad = client.post(f"/api/runs/{rid}/fx", json={"fx_rates": {"JPY": {}}})
    assert bad.status_code == 409 and bad.get_json()["rolled_back"] is True
    good = client.post(f"/api/runs/{rid}/fx", json={"fx_rates": FX_FULL})
    assert good.status_code == 200 and good.get_json()["ok"] is True


def test_http_missing_run_404(client):
    assert client.get("/api/runs/nope/adjustments").status_code == 404
    assert client.post("/api/runs/nope/fx", json={}).status_code == 404


def test_compensation_page_renders_latest_conclusion(client, fx_run):
    rid = fx_run["run_id"]
    client.post(f"/api/runs/{rid}/adjustments",
                json={"parcel": "1003", "scan_id": "0124", "batch_date": "2026-11-18",
                      "amount": "800", "currency": "JPY"})
    page = client.get("/compensation")
    assert page.status_code == 200
    html = page.data.decode()
    assert "合同基准日" in html and "补传重算" in html and "14.07" in html
    # 所有模块页在结论载荷下都不能因缺 version/decision 崩掉
    for slug in ("scans", "nodes", "timezones", "paths", "timeliness", "archives"):
        assert client.get(f"/{slug}").status_code == 200


def test_archive_uses_latest_conclusion(client, fx_run):
    import csv
    import io
    import json
    rid = fx_run["run_id"]
    client.post(f"/api/runs/{rid}/adjustments",
                json={"parcel": "1003", "scan_id": "0124", "batch_date": "2026-11-18",
                      "amount": "800", "currency": "JPY"})
    arc = client.get(f"/api/runs/{rid}/archive")
    rows = list(csv.reader(io.StringIO(arc.data.decode("utf-8-sig"))))
    evidence = [json.loads(x[6]) for x in rows[1:] if "1003" in x[6]]
    assert evidence and evidence[0]["adjustment_total"] == "14.07"
    assert evidence[0]["contract_rate_date"] == "2026-11-04"
