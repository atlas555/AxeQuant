"""Per-bar equity snapshots + drift-vs-backtest computation."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def record_snapshot(run_id: str, equity: float, position: dict[str, Any],
                    db_session, ts=None) -> None:
    """Insert one row into bts_paper_snapshots (or bts_live_snapshots via run_id)."""
    from app.services.backtestsys_plugin.api.common import utcnow
    from app.services.backtestsys_plugin.api.models import PaperSnapshot

    db_session.add(PaperSnapshot(
        run_id=run_id, ts=ts or utcnow(),
        equity=float(equity),
        position_size=float(position.get("size") or 0.0),
        position_side=position.get("side"),
    ))
    # Commit is the caller's responsibility (batching friendly)


def compute_drift(live_equity: float, expected_equity: float,
                  initial_capital: float, trivial_threshold: float = 1.0) -> tuple[bool, float]:
    """Return (is_drifting, drift_fraction).

    Drift = |live_pnl - expected_pnl| / |expected_pnl|. When both PnLs are
    < trivial_threshold in absolute value, return (False, 0.0) — window too
    small to infer drift.
    """
    expected_pnl = expected_equity - initial_capital
    live_pnl = live_equity - initial_capital
    if abs(expected_pnl) < trivial_threshold:
        return (False, 0.0)
    drift = abs(live_pnl - expected_pnl) / abs(expected_pnl)
    return (drift > 0.02, float(drift))


def compute_sharpe_from_snapshots(snapshots: list, periods_per_year: int = 35040) -> float:
    """Annualized Sharpe from a sequence of per-bar equity values.

    `periods_per_year` defaults to 15-min bars (35,040/yr). Pass in the right
    value for other timeframes.
    """
    import math
    equities = [float(s["equity"]) for s in snapshots] if snapshots and isinstance(snapshots[0], dict) \
        else [float(getattr(s, "equity", s)) for s in snapshots]
    if len(equities) < 2:
        return 0.0
    returns = [equities[i] / equities[i - 1] - 1.0 for i in range(1, len(equities))
               if equities[i - 1] > 0]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def compute_max_drawdown(snapshots: list) -> float:
    """Max drawdown as a fraction (0.0-1.0) from an equity time series."""
    equities = [float(s["equity"]) for s in snapshots] if snapshots and isinstance(snapshots[0], dict) \
        else [float(getattr(s, "equity", s)) for s in snapshots]
    if not equities:
        return 0.0
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
    return float(max_dd)
