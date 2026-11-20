import sqlite3, json
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError
from app.values import number
from app.fx import _date_text


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "records.db", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    # 补传调整台账：只追加。唯一约束保证同一补传（批次+扫描号+批次日+金额）不重复冲销
    db.execute("""CREATE TABLE IF NOT EXISTS adjustments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL, parcel TEXT NOT NULL, scan_id TEXT NOT NULL,
        batch_date TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '补传重算',
        created_version INTEGER NOT NULL DEFAULT 1,
        UNIQUE(run_id, parcel, scan_id, batch_date, amount))""")
    # 汇率切换结论台账：每次成功切换追加一行；失败切换不写入，页面回退到最后一条可用结论
    db.execute("""CREATE TABLE IF NOT EXISTS fx_conclusions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL, version INTEGER NOT NULL,
        fx_params TEXT NOT NULL, payload TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        note TEXT NOT NULL DEFAULT '')""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_fx_run ON fx_conclusions(run_id, id)")
    # 原始输入字节单独保存，供补传/汇率切换后按同一输入重算；永不被重算改写
    db.execute("CREATE TABLE IF NOT EXISTS raw_inputs(run_id TEXT PRIMARY KEY, raw BLOB NOT NULL)")
    return db


def save(run_id, payload, raw=None):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",
                   (run_id, json.dumps(payload, ensure_ascii=False)))
        if raw is not None:
            db.execute("INSERT OR IGNORE INTO raw_inputs(run_id,raw) VALUES (?,?)", (run_id, raw))
    return get(run_id)


def get_raw(run_id):
    with connect() as db:
        row = db.execute("SELECT raw FROM raw_inputs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise DomainError("RAW_UNAVAILABLE", "原始输入未随批次保存，无法重算")
    return row[0]


def get(run_id):
    with connect() as db:
        row = db.execute("SELECT payload,version,decision FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise DomainError("NOT_FOUND", "批次不存在", 404)
    return {**json.loads(row[0]), "version": row[1], "decision": row[2]}


def latest():
    with connect() as db:
        row = db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        return None
    run = get(row[0])
    # 页面默认展示最近一次可用汇率/补传结论；无切换台账时返回批次本身。
    # 结论台账只存计算载荷，版本/复核状态仍以 runs 行为准并回填。
    conclusion = latest_conclusion(run["run_id"])
    if not conclusion:
        return run
    payload = conclusion["payload"]
    payload["version"] = run["version"]
    payload["decision"] = run["decision"]
    return payload


def history():
    with connect() as db:
        return [{"run_id": i, "version": v, "decision": d}
                for i, v, d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]


def review(run_id, version, decision):
    if decision not in {"已复核", "退回复核"}:
        raise DomainError("BAD_DECISION", "复核状态无效")
    with connect() as db:
        cursor = db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",
                            (decision, run_id, version))
        if cursor.rowcount != 1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                raise DomainError("NOT_FOUND", "批次不存在", 404)
            raise DomainError("STALE_VERSION", "版本已更新，请刷新", 409)
    return get(run_id)


def list_adjustments(run_id):
    with connect() as db:
        rows = db.execute(
            "SELECT parcel,scan_id,batch_date,amount,currency,source,created_version "
            "FROM adjustments WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    return [{"parcel": p, "scan_id": s, "batch_date": b, "amount": a,
             "currency": c, "source": src, "created_version": v}
            for p, s, b, a, c, src, v in rows]


def add_adjustment(run_id, parcel, scan_id, batch_date, amount, currency, source, version):
    """登记一笔补传调整。重复重试命中唯一约束时幂等返回既有记录，不产生第二次冲销。"""
    batch_date = _date_text(batch_date)
    amount = str(number(amount))
    with connect() as db:
        try:
            db.execute(
                "INSERT INTO adjustments(run_id,parcel,scan_id,batch_date,amount,currency,source,created_version)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (run_id, str(parcel), str(scan_id), batch_date, amount,
                 str(currency).upper(), str(source), version))
        except sqlite3.IntegrityError:
            existing = db.execute(
                "SELECT parcel,scan_id,batch_date,amount,currency,source,created_version "
                "FROM adjustments WHERE run_id=? AND parcel=? AND scan_id=? AND batch_date=? AND amount=?",
                (run_id, str(parcel), str(scan_id), batch_date, amount)).fetchone()
            if not existing:
                raise DomainError("ADJUSTMENT_CONFLICT", "补传登记冲突")
            return {"parcel": existing[0], "scan_id": existing[1], "batch_date": existing[2],
                    "amount": existing[3], "currency": existing[4], "source": existing[5],
                    "created_version": existing[6], "deduplicated": True}
    return {"parcel": str(parcel), "scan_id": str(scan_id), "batch_date": batch_date,
            "amount": amount, "currency": str(currency).upper(), "source": str(source),
            "created_version": version, "deduplicated": False}


def latest_conclusion(run_id):
    with connect() as db:
        row = db.execute(
            "SELECT fx_params,payload,version,note FROM fx_conclusions WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run_id,)).fetchone()
    if not row:
        return None
    return {"fx_params": json.loads(row[0]), "payload": json.loads(row[1]),
            "version": row[2], "note": row[3]}


def conclusions(run_id):
    with connect() as db:
        rows = db.execute(
            "SELECT id,version,fx_params,note,created_at FROM fx_conclusions WHERE run_id=? ORDER BY id",
            (run_id,)).fetchall()
    return [{"seq": i, "version": v, "fx_params": json.loads(p), "note": n, "created_at": t}
            for i, v, p, n, t in rows]


def commit_conclusion(run_id, version, fx_params, payload, note):
    with connect() as db:
        db.execute(
            "INSERT INTO fx_conclusions(run_id,version,fx_params,payload,note) VALUES (?,?,?,?,?)",
            (run_id, version, json.dumps(fx_params, ensure_ascii=False),
             json.dumps(payload, ensure_ascii=False), str(note)))
