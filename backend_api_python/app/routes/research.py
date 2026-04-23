"""Flask blueprint for /api/research/* endpoints.

Registered from app/__init__.py via the AxeQuant bootstrap patch.

Endpoints:
  POST /api/research/defense              — submit defense job
  GET  /api/research/defense/<job_id>     — poll defense job

  POST /api/research/autoresearch         — submit autoresearch job
  GET  /api/research/autoresearch/<job_id>
  POST /api/research/autoresearch/<job_id>/candidate/<rank>/promote

  POST /api/research/paper/promote        — promote strategy to paper
  GET  /api/research/paper/<run_id>
  POST /api/research/paper/<run_id>/stop

  POST /api/research/live/request-confirmation
  POST /api/research/live/promote         — requires confirmation_token
  GET  /api/research/live/<run_id>
  POST /api/research/live/<run_id>/kill   — emergency manual kill
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

research_bp = Blueprint("bts_research", __name__, url_prefix="/api/research")


def _current_user_id() -> int | None:
    """Hook for auth. Upstream QD uses flask_jwt_extended; fall back to header."""
    try:
        from flask_jwt_extended import get_jwt_identity
        return get_jwt_identity()
    except Exception:  # noqa: BLE001
        hdr = request.headers.get("X-User-Id")
        return int(hdr) if hdr and hdr.isdigit() else None


# ── Defense (Phase 2) ───────────────────────────────────────────────

@research_bp.route("/defense", methods=["POST"])
def submit_defense():
    from app.services.backtestsys_plugin.api.defense_service import enqueue_defense_job
    from app.extensions import db

    payload = request.get_json() or {}
    if "config" not in payload or "param_grid" not in payload:
        return jsonify({"error": "config and param_grid required"}), 400
    job_id = enqueue_defense_job(payload, user_id=_current_user_id(), db_session=db.session)
    return jsonify({
        "job_id": job_id, "status": "queued",
        "polling_url": f"/api/research/defense/{job_id}",
    })


@research_bp.route("/defense/<job_id>", methods=["GET"])
def get_defense(job_id):
    from app.services.backtestsys_plugin.api.models import DefenseReport
    rec = DefenseReport.query.filter_by(job_id=job_id).first()
    if rec is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(rec.to_dict())


# ── Autoresearch (Phase 3) ──────────────────────────────────────────

@research_bp.route("/autoresearch", methods=["POST"])
def submit_autoresearch():
    from app.services.backtestsys_plugin.api.autoresearch_service import enqueue_autoresearch_job
    from app.extensions import db

    payload = request.get_json() or {}
    try:
        job_id = enqueue_autoresearch_job(payload, user_id=_current_user_id(), db_session=db.session)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"job_id": job_id, "status": "queued"})


@research_bp.route("/autoresearch/<job_id>", methods=["GET"])
def get_autoresearch(job_id):
    from app.services.backtestsys_plugin.api.models import AutoresearchReport
    rec = AutoresearchReport.query.filter_by(job_id=job_id).first()
    if rec is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(rec.to_dict())


@research_bp.route("/autoresearch/<job_id>/candidate/<int:rank>/promote", methods=["POST"])
def promote_candidate(job_id, rank):
    from app.services.backtestsys_plugin.api.autoresearch_service import promote_candidate_to_paper
    from app.extensions import db

    try:
        run_id = promote_candidate_to_paper(job_id, rank, user_id=_current_user_id(),
                                            db_session=db.session, payload=request.get_json() or {})
    except (PermissionError, ValueError) as e:
        return jsonify({"error": str(e)}), 403 if isinstance(e, PermissionError) else 400
    return jsonify({"run_id": run_id})


# ── Paper (Phase 4) ─────────────────────────────────────────────────

@research_bp.route("/paper/promote", methods=["POST"])
def promote_paper():
    from app.services.backtestsys_plugin.api.paper_service import promote_strategy_to_paper
    from app.extensions import db

    payload = request.get_json() or {}
    try:
        run_id = promote_strategy_to_paper(payload, user_id=_current_user_id(), db_session=db.session)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"run_id": run_id})


@research_bp.route("/paper/<run_id>", methods=["GET"])
def get_paper(run_id):
    from app.services.backtestsys_plugin.api.models import PaperRun
    rec = PaperRun.query.filter_by(id=run_id).first()
    if rec is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": rec.id, "status": rec.status, "exchange": rec.exchange,
                    "testnet": rec.testnet, "initial_capital": rec.initial_capital,
                    "started_at": rec.started_at.isoformat() if rec.started_at else None,
                    "drift_violations": rec.drift_violations})


@research_bp.route("/paper/<run_id>/stop", methods=["POST"])
def stop_paper(run_id):
    from app.services.backtestsys_plugin.api.paper_service import stop_paper_run
    from app.extensions import db
    stop_paper_run(run_id, db_session=db.session)
    return jsonify({"status": "stopping"})


# ── Live (Phase 5) ──────────────────────────────────────────────────

@research_bp.route("/live/request-confirmation", methods=["POST"])
def request_live_confirmation():
    from app.services.backtestsys_plugin.api.live_service import issue_confirmation_token

    payload = request.get_json() or {}
    token = issue_confirmation_token(user_id=_current_user_id(),
                                     paper_run_id=payload.get("paper_run_id"))
    return jsonify({"confirmation_token": token, "expires_in": 300})


@research_bp.route("/live/promote", methods=["POST"])
def promote_live():
    from app.services.backtestsys_plugin.api.live_service import promote_to_live
    from app.extensions import db

    payload = request.get_json() or {}
    try:
        run_id = promote_to_live(payload, user_id=_current_user_id(), db_session=db.session)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"run_id": run_id})


@research_bp.route("/live/<run_id>", methods=["GET"])
def get_live(run_id):
    from app.services.backtestsys_plugin.api.models import LiveRun
    rec = LiveRun.query.filter_by(id=run_id).first()
    if rec is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": rec.id, "status": rec.status, "exchange": rec.exchange,
                    "capital": rec.capital, "kill_reason": rec.kill_reason,
                    "started_at": rec.started_at.isoformat() if rec.started_at else None,
                    "killed_at": rec.killed_at.isoformat() if rec.killed_at else None,
                    "qualification": rec.qualification})


@research_bp.route("/live/<run_id>/kill", methods=["POST"])
def kill_live(run_id):
    from app.services.backtestsys_plugin.api.live_service import manual_kill
    from app.extensions import db
    manual_kill(run_id, db_session=db.session, user_id=_current_user_id())
    return jsonify({"status": "killed"})


def register_research_blueprint(app):
    """Called from app/__init__.py to wire the blueprint into the Flask app."""
    if "bts_research" in app.blueprints:
        return
    app.register_blueprint(research_bp)
    log.info("AxeQuant: registered bts_research blueprint at /api/research")
