import csv, io, json
from app import store


def _effective_run(run):
    """\u5f52\u6863\u4ee5\u6700\u8fd1\u4e00\u6b21\u53ef\u7528\u6c47\u7387/\u8865\u4f20\u7ed3\u8bba\u4e3a\u51c6\uff1b\u65e0\u5207\u6362\u53f0\u8d26\u65f6\u7528\u6279\u6b21\u672c\u8eab\u3002"""
    conclusion = store.latest_conclusion(run["run_id"])
    return conclusion["payload"] if conclusion else run


def build_archive(run):
    run = _effective_run(run)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["run_id", "input_sha256", "rule_version", "kind", "source_lines", "reason", "evidence"])
    meta = run["manifest"]
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"], meta["input_sha256"], meta["rule_version"], "accepted",
                         json.dumps(row.get("_sources", [])), row.get("status", ""),
                         json.dumps(row, ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine", []):
        writer.writerow([run["run_id"], meta["input_sha256"], meta["rule_version"], "quarantine",
                         json.dumps(row.get("_sources", [])), row.get("reason", ""),
                         json.dumps(row, ensure_ascii=False)])
    return "\ufeff" + stream.getvalue()
