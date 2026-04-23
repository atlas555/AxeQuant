"""Unit tests for Phase 5 modules: qualification, kill switch, audit log, live token flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


# ── Qualification ───────────────────────────────────────────────────

class _FakePaperRun:
    def __init__(self, started_at, status="running"):
        self.started_at = started_at
        self.status = status
        self.id = "paper_test"


def test_qualification_rejects_young_run():
    from app.services.backtestsys_plugin.live.qualification import (
        QualificationConfig, check_qualification,
    )
    pr = _FakePaperRun(datetime.now(timezone.utc) - timedelta(days=5))
    res = check_qualification(pr, [{"equity": 10000}], 2.5, 0.1, 30)
    assert not res.qualified
    assert any("age" in r for r in res.reasons)


def test_qualification_rejects_high_drift():
    from app.services.backtestsys_plugin.live.qualification import check_qualification
    pr = _FakePaperRun(datetime.now(timezone.utc) - timedelta(days=20))
    # 100 bars, fluctuating; compute_sharpe returns some value, which we can't
    # predict exactly — seed it with a trivial drift scenario by asking for
    # backtest_sharpe that's far from whatever our data gives
    snapshots = [{"equity": 10_000 + i} for i in range(100)]  # monotonic
    # Monotonic → variance = 0 → Sharpe = 0
    # Backtest OOS Sharpe 2.5 → drift = 100%
    res = check_qualification(pr, snapshots, 2.5, 0.1, 30)
    assert not res.qualified
    assert any("Sharpe drift" in r for r in res.reasons)


def test_qualification_rejects_too_few_trades():
    from app.services.backtestsys_plugin.live.qualification import check_qualification
    pr = _FakePaperRun(datetime.now(timezone.utc) - timedelta(days=20))
    res = check_qualification(pr, [{"equity": 10000}], 0.01, 0.1, n_trades=5)
    assert not res.qualified
    assert any("trades" in r for r in res.reasons)


# ── Kill switch ─────────────────────────────────────────────────────

def test_kill_switch_not_fire_single_breach():
    from app.services.backtestsys_plugin.live.kill_switch import (
        KillSwitchConfig, KillSwitchMonitor,
    )
    m = KillSwitchMonitor(KillSwitchConfig(consecutive_breaches_required=2))
    assert not m.tick(drift_pct=50, dd_ratio=1.0)


def test_kill_switch_fires_on_sustained():
    from app.services.backtestsys_plugin.live.kill_switch import (
        KillSwitchConfig, KillSwitchMonitor,
    )
    m = KillSwitchMonitor(KillSwitchConfig(consecutive_breaches_required=2,
                                            max_drift_pct=40.0))
    m.tick(drift_pct=50, dd_ratio=1.0)
    assert m.tick(drift_pct=50, dd_ratio=1.0)  # second breach fires


def test_kill_switch_resets_on_recovery():
    from app.services.backtestsys_plugin.live.kill_switch import (
        KillSwitchConfig, KillSwitchMonitor,
    )
    m = KillSwitchMonitor(KillSwitchConfig(consecutive_breaches_required=2))
    m.tick(drift_pct=50, dd_ratio=1.0)  # breach
    m.tick(drift_pct=10, dd_ratio=1.0)  # recovery
    assert not m.tick(drift_pct=50, dd_ratio=1.0)  # first breach again


def test_kill_switch_fires_only_once():
    from app.services.backtestsys_plugin.live.kill_switch import (
        KillSwitchConfig, KillSwitchMonitor,
    )
    m = KillSwitchMonitor(KillSwitchConfig(consecutive_breaches_required=1))
    assert m.tick(drift_pct=100, dd_ratio=5.0)
    assert not m.tick(drift_pct=100, dd_ratio=5.0)  # already fired


def test_kill_switch_dd_ratio_trips():
    from app.services.backtestsys_plugin.live.kill_switch import (
        KillSwitchConfig, KillSwitchMonitor,
    )
    m = KillSwitchMonitor(KillSwitchConfig(consecutive_breaches_required=1,
                                            max_dd_multiplier=2.0))
    assert m.tick(drift_pct=5, dd_ratio=3.0)  # DD breach alone triggers


# ── Token flow ──────────────────────────────────────────────────────

def test_token_issue_and_verify_succeeds():
    from app.services.backtestsys_plugin.api.live_service import (
        issue_confirmation_token, verify_confirmation_token,
    )
    t = issue_confirmation_token(user_id=7, paper_run_id="paper_ok")
    verify_confirmation_token(t, user_id=7, paper_run_id="paper_ok")  # no raise


def test_token_wrong_user_rejected():
    from app.services.backtestsys_plugin.api.live_service import (
        issue_confirmation_token, verify_confirmation_token,
    )
    t = issue_confirmation_token(user_id=7, paper_run_id="paper_ok")
    import pytest
    with pytest.raises(PermissionError):
        verify_confirmation_token(t, user_id=8, paper_run_id="paper_ok")


def test_token_wrong_run_rejected():
    from app.services.backtestsys_plugin.api.live_service import (
        issue_confirmation_token, verify_confirmation_token,
    )
    t = issue_confirmation_token(user_id=7, paper_run_id="paper_a")
    import pytest
    with pytest.raises(PermissionError):
        verify_confirmation_token(t, user_id=7, paper_run_id="paper_b")


def test_token_single_use():
    from app.services.backtestsys_plugin.api.live_service import (
        issue_confirmation_token, verify_confirmation_token,
    )
    t = issue_confirmation_token(user_id=7, paper_run_id="paper_ok")
    verify_confirmation_token(t, user_id=7, paper_run_id="paper_ok")
    import pytest
    with pytest.raises(PermissionError):
        verify_confirmation_token(t, user_id=7, paper_run_id="paper_ok")


def test_token_invalid_value_rejected():
    from app.services.backtestsys_plugin.api.live_service import verify_confirmation_token
    import pytest
    with pytest.raises(PermissionError):
        verify_confirmation_token("nope", user_id=1, paper_run_id="x")


# ── Audit hash ──────────────────────────────────────────────────────

def test_audit_hash_deterministic():
    from app.services.backtestsys_plugin.live.audit_log import _compute_hash
    h1 = _compute_hash({"run_id": "r", "event_type": "x",
                        "payload": {"a": 1}, "prev_hash": ""})
    h2 = _compute_hash({"run_id": "r", "event_type": "x",
                        "payload": {"a": 1}, "prev_hash": ""})
    assert h1 == h2
    h3 = _compute_hash({"run_id": "r", "event_type": "x",
                        "payload": {"a": 2}, "prev_hash": ""})
    assert h3 != h1
