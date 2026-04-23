"""Verdict logic — translates WFA/CPCV/DSR numbers into a single classification.

Used by Phase 2 (defense reports), Phase 3 (autoresearch candidate gating),
Phase 4 (promote-to-paper), and Phase 5 (promote-to-live).

Decision rules are intentionally conservative. Tune in `VerdictGate` only
after an incident review, not to push borderline configs through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Verdict = Literal["HEALTHY", "OVERFIT", "INCONCLUSIVE"]


@dataclass
class VerdictGate:
    """Hard thresholds a defense report must pass for a HEALTHY verdict."""

    min_wfa_oos_sharpe: float = 1.0
    min_wfa_efficiency: float = 0.5
    min_dsr: float = 0.95
    min_cpcv_pct_positive: float = 0.6
    min_cpcv_mean_sharpe: float = 0.0


def decide_verdict(result: dict[str, Any], gate: VerdictGate | None = None) -> tuple[Verdict, str]:
    """Return (verdict, reason).

    `result` is a dict with possibly-present keys `wfa`, `cpcv`, `deflated_sharpe`.
    Missing metrics are treated as non-failing (we only evaluate what was computed).
    """
    g = gate or VerdictGate()
    failures: list[str] = []

    wfa = result.get("wfa")
    if wfa:
        if wfa.get("stitched_oos_sharpe", wfa.get("mean_oos_sharpe", 0)) < g.min_wfa_oos_sharpe:
            sharpe = wfa.get("stitched_oos_sharpe", wfa.get("mean_oos_sharpe", 0))
            failures.append(f"WFA OOS Sharpe {sharpe:.2f} < {g.min_wfa_oos_sharpe}")
        if wfa.get("efficiency", 0) < g.min_wfa_efficiency:
            failures.append(f"WFA efficiency {wfa['efficiency']:.2f} < {g.min_wfa_efficiency}")

    dsr = result.get("deflated_sharpe")
    if dsr is not None:
        dsr_val = dsr if isinstance(dsr, (int, float)) else dsr.get("dsr", 0)
        if dsr_val < g.min_dsr:
            failures.append(f"DSR {dsr_val:.2f} < {g.min_dsr}")

    cpcv = result.get("cpcv")
    if cpcv:
        if cpcv.get("pct_positive_sharpe", 0) < g.min_cpcv_pct_positive:
            failures.append(f"CPCV pct_positive {cpcv['pct_positive_sharpe']:.2f} < {g.min_cpcv_pct_positive}")
        if cpcv.get("mean_oos_sharpe", 0) < g.min_cpcv_mean_sharpe:
            failures.append(f"CPCV mean_sharpe {cpcv['mean_oos_sharpe']:.2f} < {g.min_cpcv_mean_sharpe}")

    if not failures:
        return "HEALTHY", "All defense gates passed"
    if len(failures) >= 2:
        return "OVERFIT", "; ".join(failures)
    return "INCONCLUSIVE", failures[0]
