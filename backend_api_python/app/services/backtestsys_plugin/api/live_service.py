"""Live service — promote qualified paper run to real-money trading.

Safety layers (all must pass):
1. Qualification check — see live/qualification.py
2. Confirmation token — short-lived, single-use, bound to user+paper_run_id
3. Capital cap — min(user_input, LIVE_MAX_CAPITAL env)
4. Kill switch monitor attached to the new LiveRun (see workers/live_monitor_worker.py)
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from app.services.backtestsys_plugin.api.common import LIVE_QUEUE, gen_job_id, utcnow

log = logging.getLogger(__name__)


LIVE_MAX_CAPITAL = float(os.environ.get("LIVE_MAX_CAPITAL", 1000.0))
CONFIRMATION_TOKEN_TTL_SEC = 300
_ENV_SECRET = os.environ.get("AXEQUANT_LIVE_SECRET", "dev-only-do-not-use-in-prod")

_token_store: dict[str, dict[str, Any]] = {}  # token → {user_id, paper_run_id, issued_at}


# ── Token flow ──────────────────────────────────────────────────────

def issue_confirmation_token(user_id: int | None, paper_run_id: str) -> str:
    """Return a short-lived confirmation token for a specific paper_run_id."""
    token = secrets.token_urlsafe(32)
    _token_store[token] = {
        "user_id": user_id, "paper_run_id": paper_run_id,
        "issued_at": time.time(), "used": False,
    }
    _gc_tokens()
    return token


def verify_confirmation_token(token: str, user_id: int | None,
                              paper_run_id: str) -> None:
    """Raise PermissionError if invalid. Marks single-use on success."""
    rec = _token_store.get(token)
    if rec is None:
        raise PermissionError("invalid confirmation token")
    if rec["used"]:
        raise PermissionError("confirmation token already used")
    if time.time() - rec["issued_at"] > CONFIRMATION_TOKEN_TTL_SEC:
        raise PermissionError("confirmation token expired")
    if rec["user_id"] != user_id:
        raise PermissionError("confirmation token user mismatch")
    if rec["paper_run_id"] != paper_run_id:
        raise PermissionError("confirmation token paper_run mismatch")
    rec["used"] = True


def _gc_tokens() -> None:
    now = time.time()
    stale = [t for t, r in _token_store.items()
             if now - r["issued_at"] > CONFIRMATION_TOKEN_TTL_SEC * 2]
    for t in stale:
        _token_store.pop(t, None)


# ── Promote flow ────────────────────────────────────────────────────

def promote_to_live(payload: dict[str, Any], user_id: int | None,
                    db_session) -> str:
    """Called via POST /api/research/live/promote. All gates enforced here."""
    from app.services.backtestsys_plugin.api.models import LiveRun, PaperRun
    from app.services.backtestsys_plugin.live.qualification import (
        QualificationConfig, check_qualification,
    )

    paper_run_id = payload.get("paper_run_id")
    token = payload.get("confirmation_token")
    requested_capital = float(payload.get("capital", 0))

    if not paper_run_id or not token:
        raise ValueError("paper_run_id and confirmation_token required")

    verify_confirmation_token(token, user_id, paper_run_id)

    paper = db_session.query(PaperRun).filter_by(id=paper_run_id).first()
    if paper is None:
        raise ValueError(f"paper run {paper_run_id} not found")

    # Gate 1: qualification
    snapshots = _load_snapshots(paper_run_id, db_session)
    backtest_oos_sharpe, backtest_max_dd, n_trades = _load_backtest_reference(paper, db_session)
    qual = check_qualification(
        paper, snapshots, backtest_oos_sharpe, backtest_max_dd, n_trades,
        cfg=QualificationConfig(),
    )
    if not qual.qualified:
        raise PermissionError(f"not qualified: {qual.reasons}")

    # Gate 2: capital cap
    effective_capital = min(requested_capital, LIVE_MAX_CAPITAL)
    if effective_capital <= 0:
        raise ValueError("capital must be positive")
    if requested_capital > LIVE_MAX_CAPITAL:
        log.warning("capital request %s clamped to env cap %s",
                    requested_capital, LIVE_MAX_CAPITAL)

    live_run_id = gen_job_id("live")
    live = LiveRun(
        id=live_run_id, user_id=user_id, paper_run_id=paper_run_id,
        strategy_id=paper.strategy_id, params=paper.params,
        exchange=paper.exchange, capital=effective_capital,
        status="starting", config=paper.config,
        qualification=qual.to_dict(), started_at=utcnow(),
    )
    db_session.add(live); db_session.commit()

    # Audit + alert + enqueue
    from app.services.backtestsys_plugin.live.audit_log import log_event
    from app.services.backtestsys_plugin.live.notifications import send_urgent
    log_event(live_run_id, "live_started",
              {"paper_run_id": paper_run_id, "capital": effective_capital,
               "user_id": user_id}, db_session)
    try:
        send_urgent(live, f"LIVE STARTED: ${effective_capital}")
    except Exception:  # noqa: BLE001
        pass

    LIVE_QUEUE.enqueue(live_run_id)
    return live_run_id


def manual_kill(run_id: str, db_session, user_id: int | None = None) -> None:
    """Operator pull-the-cord. Marks run stopping; monitor worker flatten on next tick."""
    from app.services.backtestsys_plugin.api.models import LiveRun
    from app.services.backtestsys_plugin.live.audit_log import log_event

    run = db_session.query(LiveRun).filter_by(id=run_id).first()
    if run is None:
        raise ValueError(f"live run {run_id} not found")
    run.status = "killing"
    run.kill_reason = "manual operator kill"
    db_session.commit()
    log_event(run_id, "manual_kill_requested", {"user_id": user_id}, db_session)


# ── Helpers ─────────────────────────────────────────────────────────

def _load_snapshots(paper_run_id: str, db_session) -> list:
    from app.services.backtestsys_plugin.api.models import PaperSnapshot
    return (db_session.query(PaperSnapshot)
            .filter_by(run_id=paper_run_id)
            .order_by(PaperSnapshot.ts.asc())
            .all())


def _load_backtest_reference(paper_run, db_session) -> tuple[float, float, int]:
    """Pull the backtest OOS Sharpe / max DD / trade count for the strategy.

    Looks up the latest DefenseReport for strategy_id if available, otherwise
    falls back to strategy metadata. Returns (0.0, 0.0, 0) if nothing found
    — qualification check will then fail on drift/DD/trades criteria.
    """
    from app.services.backtestsys_plugin.api.models import DefenseReport

    rep = (db_session.query(DefenseReport)
           .filter_by(strategy_id=paper_run.strategy_id, status="done")
           .order_by(DefenseReport.created_at.desc())
           .first())
    if rep and rep.result:
        wfa = rep.result.get("wfa") or {}
        return (
            float(wfa.get("stitched_oos_sharpe", 0.0)),
            0.0,  # max DD not currently in defense report; populate in Phase 5 hardening
            0,    # n_trades likewise
        )
    return 0.0, 0.0, 0
