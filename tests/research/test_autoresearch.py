"""Autoresearch service tests with stub metric_fn (no real backtest)."""

from __future__ import annotations


def test_deep_merge_basic():
    from app.services.backtestsys_plugin.api.autoresearch_service import _deep_merge
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 20}, "e": 5}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": {"c": 20, "d": 3}, "e": 5}


def test_deep_merge_dotted_keys():
    from app.services.backtestsys_plugin.api.autoresearch_service import _deep_merge
    base = {"tp_fracs": {"long1": 1.0, "long2": 0.5}}
    override = {"tp_fracs.long1": 0.75}
    result = _deep_merge(base, override)
    assert result["tp_fracs"]["long1"] == 0.75
    assert result["tp_fracs"]["long2"] == 0.5


def test_autoresearch_picks_higher_metric():
    """With a trivial metric_fn, optimizer should find the value that maximizes."""
    from app.services.backtestsys_plugin.api.autoresearch_service import (
        AutoresearchBudget, AutoresearchRequest, run_autoresearch,
    )

    # Peak at n=7
    def metric(cfg):
        return -abs(cfg.get("n", 0) - 7)

    req = AutoresearchRequest(
        config={"n": 0},
        param_space={"n": {"type": "int", "range": [0, 10], "step": 1, "layer": 1}},
        budget=AutoresearchBudget(max_iterations=50),
        defense_enabled=False,
    )
    result = run_autoresearch(req, metric)
    assert result["candidates"]
    top = result["candidates"][0]
    # Should have found n near the peak
    best_n = top["params"].get("n")
    assert abs(best_n - 7) <= 1, f"expected n≈7, got {best_n}"


def test_autoresearch_improvement_pct_reported():
    from app.services.backtestsys_plugin.api.autoresearch_service import (
        AutoresearchRequest, run_autoresearch, AutoresearchBudget,
    )

    def metric(cfg):
        return float(cfg.get("x", 0)) * 2.0

    req = AutoresearchRequest(
        config={"x": 1},
        param_space={"x": {"type": "int", "range": [1, 10], "step": 1, "layer": 1}},
        budget=AutoresearchBudget(max_iterations=30),
        defense_enabled=False,
    )
    result = run_autoresearch(req, metric)
    assert result["improvement_pct"] > 0
    assert result["baseline_score"] == 2.0  # x=1 → 1*2


def test_enqueue_rejects_oversized_space():
    from app.services.backtestsys_plugin.api.autoresearch_service import (
        enqueue_autoresearch_job,
    )
    huge = {
        "config": {},
        "param_space": {f"p{i}": {"type": "int", "range": [0, 200], "step": 1} for i in range(3)},
    }
    import pytest
    with pytest.raises(ValueError):
        enqueue_autoresearch_job(huge, user_id=1, db_session=None)
