from flask import Blueprint, request, jsonify
from app.errors import DomainError
from app.pipeline import stage_preview
from app import store, reconcile

bp = Blueprint("compensation", __name__)


@bp.route("/api/compensation", methods=["GET", "POST"])
def preview():
    payload = request.get_json(silent=True) if request.method == "POST" else {}
    if payload is None or not isinstance(payload, dict):
        raise DomainError("BAD_BODY", "请求需要对象")
    return jsonify(stage_preview(5, payload))


@bp.route("/api/runs/<run_id>/fx", methods=["POST"])
def switch_fx(run_id):
    """切换合同基准日/汇率表并整批重算；失败回滚上一可用结论。"""
    payload = request.get_json(silent=True) or {}
    # 缺省或显式 null：恢复内置默认表；对象：权威替换；其他类型：服务层判错回滚
    fx_params = payload.get("fx_rates") if "fx_rates" in payload else None
    outcome = reconcile.switch_fx(run_id, fx_params, payload.get("note", "汇率切换"))
    return jsonify(outcome), (200 if outcome["ok"] else 409)


@bp.route("/api/runs/<run_id>/adjustments", methods=["GET", "POST"])
def adjustments(run_id):
    # 先校验批次存在，不存在由 store.get 抛 404
    store.get(run_id)
    if request.method == "GET":
        return jsonify({"adjustments": store.list_adjustments(run_id),
                        "conclusions": store.conclusions(run_id)})
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise DomainError("BAD_BODY", "请求需要对象")
    outcome = reconcile.apply_adjustment(run_id, payload)
    return jsonify(outcome), (200 if outcome["ok"] else 409)
