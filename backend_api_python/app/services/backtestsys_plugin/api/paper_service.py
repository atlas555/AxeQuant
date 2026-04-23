"""Paper trading service — promote a strategy to testnet via QD live_trading.

Uses QD's upstream `live_trading/factory.get_exchange(...)` (unchanged) to
obtain a CCXT-backed exchange adapter. We wrap QD's execution surface with a
thin runner that knows about our ctx.signal()-aware ScriptStrategy model.

Paper trading semantics (Phase 4):
- testnet credentials only (enforced at validation)
- position-size cap per trade (gate against runaway sizing bugs)
- periodic drift check vs offline-replayed backtest
- manual emergency stop via /paper/<run_id>/stop
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from app.services.backtestsys_plugin.api.common import (
    PAPER_QUEUE, gen_job_id, get_redis, utcnow,
)

log = logging.getLogger(__name__)


@dataclass
class PaperPromoteRequest:
    strategy_id: str
    params: dict[str, Any]
    exchange: str = "binance"
    testnet: bool = True
    initial_capital: float = 10_000.0
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    candidate_id: int | None = None
    defense_job_id: str | None = None
    config: dict[str, Any] | None = None  # full backTestSys config


# ── Promote flow ────────────────────────────────────────────────────

def promote_strategy_to_paper(payload: dict[str, Any], user_id: int | None,
                              db_session) -> str:
    """Enqueue a paper trading run. Validates inputs; no exchange calls here."""
    req = _parse_request(payload)
    _validate_paper_constraints(req)

    # Gate: if defense_job_id provided, require HEALTHY verdict
    if req.defense_job_id:
        _require_healthy_verdict(req.defense_job_id, db_session)

    from app.services.backtestsys_plugin.api.models import PaperRun

    run_id = gen_job_id("paper")
    rec = PaperRun(
        id=run_id, user_id=user_id, strategy_id=req.strategy_id,
        candidate_id=req.candidate_id, params=req.params,
        exchange=req.exchange, testnet=req.testnet,
        initial_capital=req.initial_capital, status="starting",
        config=req.config,
    )
    db_session.add(rec); db_session.commit()
    PAPER_QUEUE.enqueue(run_id)
    return run_id


def stop_paper_run(run_id: str, db_session) -> None:
    """Signal the paper runner to stop (cooperative)."""
    from app.services.backtestsys_plugin.api.models import PaperRun

    rec = db_session.query(PaperRun).filter_by(id=run_id).first()
    if rec is None:
        raise ValueError(f"paper run {run_id} not found")
    rec.status = "stopping"
    db_session.commit()
    # Publish on a channel the runner subscribes to
    try:
        get_redis().publish(f"bts:paper:stop:{run_id}", "stop")
    except Exception:  # noqa: BLE001 — status update is the source of truth
        log.warning("Redis publish failed; runner will poll status column")


# ── Validation ──────────────────────────────────────────────────────

PAPER_MAX_CAPITAL = float(os.environ.get("PAPER_MAX_CAPITAL", 100_000.0))
ALLOWED_EXCHANGES = {"binance", "bybit", "okx", "bitget", "kraken",
                     "kucoin", "gate", "htx", "coinbase"}


def _parse_request(payload: dict[str, Any]) -> PaperPromoteRequest:
    if "strategy_id" not in payload or "params" not in payload:
        raise ValueError("strategy_id and params are required")
    return PaperPromoteRequest(
        strategy_id=payload["strategy_id"],
        params=payload["params"],
        exchange=payload.get("exchange", "binance"),
        testnet=bool(payload.get("testnet", True)),
        initial_capital=float(payload.get("initial_capital", 10_000.0)),
        symbol=payload.get("symbol", "BTC/USDT"),
        timeframe=payload.get("timeframe", "15m"),
        candidate_id=payload.get("candidate_id"),
        defense_job_id=payload.get("defense_job_id"),
        config=payload.get("config"),
    )


def _validate_paper_constraints(req: PaperPromoteRequest) -> None:
    if req.exchange not in ALLOWED_EXCHANGES:
        raise ValueError(f"exchange {req.exchange!r} not in allowlist")
    if req.initial_capital <= 0 or req.initial_capital > PAPER_MAX_CAPITAL:
        raise ValueError(
            f"initial_capital {req.initial_capital} out of range (0, {PAPER_MAX_CAPITAL}]"
        )
    if not req.testnet:
        # Phase 4 is paper-only. Live promotion uses a different path with confirmation.
        raise ValueError("paper runs must use testnet=true; use /api/research/live/promote for live")


def _require_healthy_verdict(defense_job_id: str, db_session) -> None:
    from app.services.backtestsys_plugin.api.models import DefenseReport

    rec = db_session.query(DefenseReport).filter_by(job_id=defense_job_id).first()
    if rec is None:
        raise PermissionError(f"defense job {defense_job_id} not found")
    if rec.status != "done":
        raise PermissionError(f"defense job {defense_job_id} status is {rec.status}")
    verdict = (rec.result or {}).get("verdict")
    if verdict != "HEALTHY":
        raise PermissionError(f"defense verdict is {verdict!r}, not HEALTHY")
