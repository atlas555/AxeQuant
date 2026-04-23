"""Unit tests for verdict logic — pure functions, no I/O."""

from __future__ import annotations


def test_healthy_when_all_gates_pass():
    from app.services.backtestsys_plugin.api.verdict import decide_verdict

    result = {
        "wfa": {"stitched_oos_sharpe": 2.5, "efficiency": 1.2},
        "cpcv": {"pct_positive_sharpe": 0.85, "mean_oos_sharpe": 2.1},
        "deflated_sharpe": {"dsr": 0.97},
    }
    v, reason = decide_verdict(result)
    assert v == "HEALTHY"
    assert "passed" in reason.lower()


def test_overfit_when_two_or_more_gates_fail():
    from app.services.backtestsys_plugin.api.verdict import decide_verdict

    result = {
        "wfa": {"stitched_oos_sharpe": 0.4, "efficiency": 0.2},  # both fail
        "deflated_sharpe": {"dsr": 0.5},  # also fail
    }
    v, reason = decide_verdict(result)
    assert v == "OVERFIT"
    assert reason.count(";") >= 1


def test_inconclusive_when_one_gate_fails():
    from app.services.backtestsys_plugin.api.verdict import decide_verdict

    result = {
        "wfa": {"stitched_oos_sharpe": 2.5, "efficiency": 0.3},  # only efficiency fails
    }
    v, _ = decide_verdict(result)
    assert v == "INCONCLUSIVE"


def test_missing_metrics_not_penalized():
    from app.services.backtestsys_plugin.api.verdict import decide_verdict
    # Only WFA provided — should still pass if WFA is strong
    result = {"wfa": {"stitched_oos_sharpe": 2.5, "efficiency": 1.2}}
    v, _ = decide_verdict(result)
    assert v == "HEALTHY"


def test_gate_overrides_apply():
    from app.services.backtestsys_plugin.api.verdict import VerdictGate, decide_verdict

    result = {"wfa": {"stitched_oos_sharpe": 0.8, "efficiency": 0.7}}
    strict = VerdictGate(min_wfa_oos_sharpe=1.0)
    loose = VerdictGate(min_wfa_oos_sharpe=0.5)
    assert decide_verdict(result, strict)[0] != "HEALTHY"
    assert decide_verdict(result, loose)[0] == "HEALTHY"
