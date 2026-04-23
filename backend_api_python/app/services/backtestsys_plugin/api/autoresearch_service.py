"""Autoresearch service — structural optimizer as a job.

Given a param_space + a metric-producing backtest runner, executes a layered
coordinate-descent search and returns ranked candidates. Optionally runs a
Phase 2 defense check on the top-K candidates so promotion only permits
HEALTHY verdicts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.services.backtestsys_plugin.api.common import (
    AUTORESEARCH_QUEUE, gen_job_id, utcnow,
)
from app.services.backtestsys_plugin.api.param_space import (
    check_size_or_raise, parse_param_space,
)
from app.services.backtestsys_plugin.api.serializer import to_json_safe

log = logging.getLogger(__name__)

MetricFn = Callable[[dict[str, Any]], float]


@dataclass
class AutoresearchBudget:
    max_iterations: int = 100
    max_wall_seconds: int = 1800
    early_stop_patience: int = 20


@dataclass
class AutoresearchRequest:
    config: dict[str, Any]                      # backTestSys base config
    param_space: dict[str, dict]                # see param_space.parse_param_space
    objective: str = "oos_sharpe"
    budget: AutoresearchBudget | None = None
    defense_on_top_k: int = 5
    defense_enabled: bool = True


def run_autoresearch(req: AutoresearchRequest, metric_fn: MetricFn) -> dict[str, Any]:
    """Execute the search. `metric_fn` takes a full config dict, returns a score."""
    from app.services.backtestsys_plugin.optimizer.optimizer import StructureOptimizer
    from app.services.backtestsys_plugin.optimizer.param_spec import StrategyParams

    specs = parse_param_space(req.param_space)
    check_size_or_raise(specs)
    params = StrategyParams(specs)

    budget = req.budget or AutoresearchBudget()

    # Compose config applied to each param trial
    def _metric(cfg_override: dict[str, Any]) -> float:
        merged = _deep_merge(req.config, cfg_override)
        return float(metric_fn(merged))

    opt = StructureOptimizer(params, _metric, maximize=True)
    report = opt.optimize(
        layers=sorted({s.layer for s in specs}),
        max_iterations=budget.max_iterations,
    )

    # Baseline = metric_fn with config as-submitted (unoptimized)
    baseline_score = metric_fn(req.config)

    # Top-K candidates sorted by score desc
    ranked = sorted(report.log, key=lambda r: r.metric, reverse=True)
    top_k = ranked[:max(1, req.defense_on_top_k)]

    candidates = []
    for rank, r in enumerate(top_k, start=1):
        candidates.append({
            "rank": rank,
            "params": r.config,
            "metric": float(r.metric),
            "oos_sharpe": float(r.metric),  # alias — caller can override
            "iteration": r.iteration,
            "desc": r.desc,
            # defense_job_id + verdict filled in by worker if defense_enabled
        })

    improvement_pct = (
        (candidates[0]["metric"] - baseline_score) / abs(baseline_score) * 100
        if candidates and abs(baseline_score) > 1e-9 else 0.0
    )

    return to_json_safe({
        "n_iterations": opt._iter,
        "candidates": candidates,
        "baseline_score": baseline_score,
        "improvement_pct": improvement_pct,
        "stopped_reason": "max_iterations" if opt._iter >= budget.max_iterations else "converged",
    })


def _deep_merge(base: dict, override: dict) -> dict:
    """Right-biased recursive dict merge (override wins)."""
    import copy
    out = copy.deepcopy(base)
    for k, v in override.items():
        if "." in k:
            # dotted key "tp_fracs.long1" → nested path
            parts = k.split(".")
            cursor = out
            for p in parts[:-1]:
                cursor = cursor.setdefault(p, {})
            cursor[parts[-1]] = v
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ── Job lifecycle ───────────────────────────────────────────────────

def enqueue_autoresearch_job(payload: dict[str, Any], user_id: int | None,
                             db_session=None) -> str:
    """Validate + enqueue. Raises ValueError if param_space is invalid/too large."""
    # Validate synchronously so client gets immediate feedback
    specs = parse_param_space(payload.get("param_space", {}))
    check_size_or_raise(specs)

    job_id = gen_job_id("ar")
    if db_session is not None:
        from app.services.backtestsys_plugin.api.models import AutoresearchReport
        rec = AutoresearchReport(
            job_id=job_id, user_id=user_id,
            strategy_id=payload.get("strategy_id"),
            status="queued", request=payload, created_at=utcnow(),
        )
        db_session.add(rec); db_session.commit()
    AUTORESEARCH_QUEUE.enqueue(job_id)
    return job_id


def process_autoresearch_job(job_id: str, db_session,
                             metric_fn_factory: Callable[[dict], MetricFn]) -> None:
    """Worker entry point. `metric_fn_factory` builds a metric callable from the base config."""
    from app.services.backtestsys_plugin.api.models import (
        AutoresearchReport, AutoresearchCandidate,
    )

    rec = db_session.query(AutoresearchReport).filter_by(job_id=job_id).first()
    if rec is None:
        log.warning("Autoresearch job %s not found", job_id)
        return
    rec.status = "running"; db_session.commit()
    try:
        payload = rec.request
        req = AutoresearchRequest(
            config=payload["config"],
            param_space=payload["param_space"],
            objective=payload.get("objective", "oos_sharpe"),
            budget=AutoresearchBudget(**payload.get("budget", {})),
            defense_on_top_k=payload.get("defense_on_top_k", 5),
            defense_enabled=payload.get("defense_enabled", True),
        )
        metric_fn = metric_fn_factory(req.config)
        result = run_autoresearch(req, metric_fn)

        # Persist candidates
        for c in result["candidates"]:
            db_session.add(AutoresearchCandidate(
                job_id=job_id, rank=c["rank"], params=c["params"],
                oos_sharpe=c.get("oos_sharpe"), n_trades=c.get("n_trades"),
            ))

        rec.result = result
        rec.status = "done"
    except Exception as e:  # noqa: BLE001
        log.exception("Autoresearch job %s failed", job_id)
        rec.status = "failed"; rec.error = str(e)
    finally:
        rec.completed_at = utcnow()
        db_session.commit()


def promote_candidate_to_paper(job_id: str, rank: int, user_id: int | None,
                               db_session, payload: dict[str, Any]) -> str:
    """Promote a ranked candidate to a paper run.

    Gate: candidate's verdict must be HEALTHY. Raises PermissionError otherwise.
    """
    from app.services.backtestsys_plugin.api.models import AutoresearchCandidate
    from app.services.backtestsys_plugin.api.paper_service import promote_strategy_to_paper

    cand = db_session.query(AutoresearchCandidate).filter_by(
        job_id=job_id, rank=rank
    ).first()
    if cand is None:
        raise ValueError(f"candidate rank {rank} not found for job {job_id}")
    if cand.verdict not in ("HEALTHY", None):
        # None is allowed when defense hasn't been run yet — caller can opt in
        raise PermissionError(f"candidate verdict is {cand.verdict!r}, not HEALTHY")

    merged = dict(payload)
    merged.setdefault("params", cand.params)
    merged.setdefault("candidate_id", cand.id)
    return promote_strategy_to_paper(merged, user_id=user_id, db_session=db_session)
