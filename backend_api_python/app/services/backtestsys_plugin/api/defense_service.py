"""Defense service — runs WFA, CPCV, Deflated Sharpe on a strategy config.

Accepts a backTestSys-style YAML config (or dict equivalent) + a param_grid
for WFA, returns a full defense report.

**Current scope:** operates on vendored backTestSys configs (YAML-shaped dicts).
Direct QD-strategy-native integration (compiling QD ScriptStrategy → backtest
runner) is tracked as a future refinement — the hybrid runner is non-trivial
and not required for the core value of Phase 2 (a defense-report UI inside QD).
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any

import yaml

from app.services.backtestsys_plugin.api.common import gen_job_id, utcnow
from app.services.backtestsys_plugin.api.serializer import (
    cpcv_report_to_dict,
    to_json_safe,
    wfa_report_to_dict,
)
from app.services.backtestsys_plugin.api.verdict import VerdictGate, decide_verdict

log = logging.getLogger(__name__)


@dataclass
class DefenseRequest:
    config: dict[str, Any]                # backTestSys config (YAML-equivalent)
    param_grid: dict[str, list]           # WFA / CPCV param grid
    mode: str = "full"                    # "wfa" | "cpcv" | "full"
    wfa_config: dict[str, Any] | None = None
    cpcv_config: dict[str, Any] | None = None


def run_defense(request: DefenseRequest) -> dict[str, Any]:
    """Execute the requested defense checks. Returns a JSON-safe result dict."""
    from app.services.backtestsys_plugin.defense.walk_forward import (
        WalkForwardAnalyzer, WFAConfig,
    )
    from app.services.backtestsys_plugin.defense.cpcv import (
        CPCVAnalyzer, CPCVConfig,
    )
    from app.services.backtestsys_plugin.defense.deflated_sharpe import DeflatedSharpeRatio

    # Vendored analyzers want a YAML path. Dump the dict config to a temp file.
    cfg_path = _dump_temp_yaml(request.config)
    try:
        result: dict[str, Any] = {}

        if request.mode in ("wfa", "full"):
            wfa_cfg = WFAConfig(**(request.wfa_config or {}))
            wfa_report = WalkForwardAnalyzer(wfa_cfg).run(cfg_path, request.param_grid)
            result["wfa"] = wfa_report_to_dict(wfa_report)

        if request.mode in ("cpcv", "full"):
            cpcv_cfg = CPCVConfig(**(request.cpcv_config or {}))
            cpcv_report = CPCVAnalyzer(cpcv_cfg).run(cfg_path, request.param_grid)
            result["cpcv"] = cpcv_report_to_dict(cpcv_report)

        if request.mode == "full" and "wfa" in result:
            # DSR requires an observed sharpe + returns series + n_trials
            wfa = result["wfa"]
            n_trials = _count_param_combinations(request.param_grid) * wfa["n_rounds"]
            returns = _stitch_round_returns(wfa)
            if returns:
                dsr = DeflatedSharpeRatio().compute(
                    observed_sharpe=wfa["stitched_oos_sharpe"],
                    n_trials=n_trials,
                    returns=returns,
                )
                result["deflated_sharpe"] = {"dsr": float(dsr), "n_trials": n_trials}

        verdict, reason = decide_verdict(result, VerdictGate())
        result["verdict"] = verdict
        result["verdict_reason"] = reason
        return to_json_safe(result)
    finally:
        try:
            os.unlink(cfg_path)
        except OSError:
            pass


def _dump_temp_yaml(cfg: dict[str, Any]) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="bts_defense_")
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(cfg, f)
    return path


def _count_param_combinations(grid: dict[str, list]) -> int:
    n = 1
    for v in grid.values():
        n *= max(1, len(v) if hasattr(v, "__len__") else 1)
    return n


def _stitch_round_returns(wfa_dict: dict[str, Any]) -> list[float]:
    """Approximate per-round returns from combined_oos_equity deltas.

    Not perfect — actual stitching should normalize across rounds — but
    sufficient for DSR skew/kurt estimation.
    """
    eq = wfa_dict.get("combined_oos_equity", [])
    if len(eq) < 2:
        return []
    return [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] != 0]


# ── Job lifecycle (Flask-adjacent; thin wrappers) ────────────────────

def enqueue_defense_job(payload: dict[str, Any], user_id: int,
                        db_session=None) -> str:
    """Create a queued job record and push to Redis. Returns job_id."""
    from app.services.backtestsys_plugin.api.common import DEFENSE_QUEUE
    job_id = gen_job_id("def")
    if db_session is not None:
        from app.services.backtestsys_plugin.api.models import DefenseReport
        rec = DefenseReport(
            job_id=job_id, user_id=user_id, strategy_id=payload.get("strategy_id"),
            status="queued", request=payload, created_at=utcnow(),
        )
        db_session.add(rec); db_session.commit()
    DEFENSE_QUEUE.enqueue(job_id)
    return job_id


def process_defense_job(job_id: str, db_session) -> None:
    """Worker entry point. Loads record, runs defense, writes result back."""
    from app.services.backtestsys_plugin.api.models import DefenseReport
    rec = db_session.query(DefenseReport).filter_by(job_id=job_id).first()
    if rec is None:
        log.warning("Defense job %s not found in DB", job_id)
        return
    rec.status = "running"; db_session.commit()
    try:
        req = DefenseRequest(**{k: v for k, v in rec.request.items() if k in DefenseRequest.__dataclass_fields__})
        result = run_defense(req)
        rec.result = result
        rec.status = "done"
    except Exception as e:  # noqa: BLE001
        log.exception("Defense job %s failed", job_id)
        rec.status = "failed"
        rec.error = str(e)
    finally:
        rec.completed_at = utcnow()
        db_session.commit()
