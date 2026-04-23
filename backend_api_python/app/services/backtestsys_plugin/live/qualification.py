"""Qualification gate — decide whether a paper run is ready for live money.

Hard criteria (can only be relaxed via explicit config, never silently):
- ≥ min_days of continuous paper running (default 14)
- Live Sharpe within ±max_sharpe_drift_pct of backtest OOS Sharpe (default 30%)
- Max drawdown ≤ max_dd_multiplier × backtest max DD (default 1.5x)
- ≥ min_trades trades (default 30)

Returns a `QualificationResult` dataclass with reasons for any failures.
Caller (`live_service.promote_to_live`) treats `qualified=False` as hard
refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QualificationConfig:
    min_days: int = 14
    max_sharpe_drift_pct: float = 30.0
    max_dd_multiplier: float = 1.5
    min_trades: int = 30


@dataclass
class QualificationResult:
    qualified: bool
    reasons: list[str] = field(default_factory=list)
    paper_sharpe: float = 0.0
    backtest_oos_sharpe: float = 0.0
    sharpe_drift_pct: float = 0.0
    paper_max_dd: float = 0.0
    backtest_max_dd: float = 0.0
    n_trades: int = 0
    age_days: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified, "reasons": self.reasons,
            "paper_sharpe": self.paper_sharpe,
            "backtest_oos_sharpe": self.backtest_oos_sharpe,
            "sharpe_drift_pct": self.sharpe_drift_pct,
            "paper_max_dd": self.paper_max_dd,
            "backtest_max_dd": self.backtest_max_dd,
            "n_trades": self.n_trades, "age_days": self.age_days,
        }


def check_qualification(
    paper_run, snapshots: list, backtest_oos_sharpe: float,
    backtest_max_dd: float, n_trades: int,
    cfg: QualificationConfig | None = None, now=None,
) -> QualificationResult:
    """Pure function — no DB. Caller fetches inputs, passes them in.

    `paper_run` only needs .started_at, .status attributes.
    `snapshots` is a list of dict/ORM rows with `equity` attribute.
    """
    import math
    from datetime import datetime, timezone
    from app.services.backtestsys_plugin.live.pnl_tracker import (
        compute_max_drawdown, compute_sharpe_from_snapshots,
    )

    cfg = cfg or QualificationConfig()
    now = now or datetime.now(timezone.utc)

    reasons: list[str] = []

    started = getattr(paper_run, "started_at", None)
    if started is None:
        age_days = 0.0
        reasons.append("paper run has no started_at")
    else:
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_days = (now - started).total_seconds() / 86400.0

    if age_days < cfg.min_days:
        reasons.append(f"age {age_days:.1f}d < required {cfg.min_days}d")

    if getattr(paper_run, "status", None) not in ("running", "stopped"):
        reasons.append(f"paper status is {paper_run.status!r}")

    paper_sharpe = compute_sharpe_from_snapshots(snapshots) if snapshots else 0.0
    drift_pct = (
        abs(paper_sharpe - backtest_oos_sharpe) / abs(backtest_oos_sharpe) * 100
        if abs(backtest_oos_sharpe) > 1e-9 else math.inf
    )
    if drift_pct > cfg.max_sharpe_drift_pct:
        reasons.append(
            f"Sharpe drift {drift_pct:.1f}% > {cfg.max_sharpe_drift_pct}%"
        )

    paper_dd = compute_max_drawdown(snapshots) if snapshots else 0.0
    if backtest_max_dd > 0 and paper_dd > backtest_max_dd * cfg.max_dd_multiplier:
        reasons.append(
            f"paper DD {paper_dd:.2%} > {cfg.max_dd_multiplier}× backtest {backtest_max_dd:.2%}"
        )

    if n_trades < cfg.min_trades:
        reasons.append(f"only {n_trades} trades, need ≥ {cfg.min_trades}")

    return QualificationResult(
        qualified=not reasons, reasons=reasons,
        paper_sharpe=paper_sharpe, backtest_oos_sharpe=backtest_oos_sharpe,
        sharpe_drift_pct=drift_pct, paper_max_dd=paper_dd,
        backtest_max_dd=backtest_max_dd, n_trades=n_trades,
        age_days=age_days,
    )
