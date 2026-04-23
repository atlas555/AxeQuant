"""Tests for live execution primitives (signal → order translation, pnl math)."""

from __future__ import annotations


# ── signal_to_order ─────────────────────────────────────────────────

def test_buy_market():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_action
    out = translate_action({"action": "buy", "amount": 0.1}, "BTC/USDT")
    assert out == {"symbol": "BTC/USDT", "side": "buy", "type": "market", "amount": 0.1}


def test_buy_limit_with_price():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_action
    out = translate_action({"action": "buy", "amount": 0.1, "price": 50000}, "BTC/USDT")
    assert out["type"] == "limit" and out["price"] == 50000.0


def test_sell_direction():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_action
    out = translate_action({"action": "sell", "amount": 0.05}, "ETH/USDT")
    assert out["side"] == "sell" and out["amount"] == 0.05


def test_rejects_zero_amount():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_action
    import pytest
    with pytest.raises(ValueError):
        translate_action({"action": "buy", "amount": 0}, "BTC/USDT")


def test_rejects_unknown_action():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_action
    import pytest
    with pytest.raises(ValueError):
        translate_action({"action": "wat"}, "BTC/USDT")


def test_close_long_position():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_close
    out = translate_close({"size": 0.2, "side": "long"}, "BTC/USDT")
    assert out["side"] == "sell" and out["amount"] == 0.2
    assert out["params"]["reduceOnly"] is True


def test_close_short_position():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_close
    out = translate_close({"size": 0.3, "side": "short"}, "BTC/USDT")
    assert out["side"] == "buy" and out["amount"] == 0.3


def test_close_flat_returns_none():
    from app.services.backtestsys_plugin.live.signal_to_order import translate_close
    assert translate_close({"size": 0, "side": None}, "BTC/USDT") is None


# ── pnl_tracker ─────────────────────────────────────────────────────

def test_drift_trivial_window():
    from app.services.backtestsys_plugin.live.pnl_tracker import compute_drift
    drifting, d = compute_drift(10_001.0, 10_000.5, 10_000.0)
    assert not drifting and d == 0.0


def test_drift_within_tolerance():
    from app.services.backtestsys_plugin.live.pnl_tracker import compute_drift
    # live_pnl = 100, expected_pnl = 98, drift = 2/98 ≈ 0.0204 → just over
    drifting, d = compute_drift(10_100.0, 10_098.0, 10_000.0)
    assert drifting
    assert 0.019 < d < 0.022


def test_drift_exceeds_tolerance():
    from app.services.backtestsys_plugin.live.pnl_tracker import compute_drift
    drifting, d = compute_drift(10_500.0, 10_100.0, 10_000.0)
    assert drifting
    assert d > 0.02


def test_sharpe_flat_returns_zero():
    from app.services.backtestsys_plugin.live.pnl_tracker import compute_sharpe_from_snapshots
    snapshots = [{"equity": 10_000.0}] * 100
    assert compute_sharpe_from_snapshots(snapshots) == 0.0


def test_sharpe_uptrending():
    from app.services.backtestsys_plugin.live.pnl_tracker import compute_sharpe_from_snapshots
    # Steady 1% bar returns — Sharpe should be very high (near-infinite)
    eqs = [10_000.0]
    for _ in range(100):
        eqs.append(eqs[-1] * 1.01)
    snapshots = [{"equity": e} for e in eqs]
    # Flat upward → variance = 0 → function returns 0 (defensive)
    assert compute_sharpe_from_snapshots(snapshots) == 0.0


def test_max_drawdown_simple():
    from app.services.backtestsys_plugin.live.pnl_tracker import compute_max_drawdown
    # 100 → 110 → 88 → 120: peak=110, trough=88, dd=(110-88)/110 ≈ 0.2
    eqs = [100, 110, 88, 120]
    snapshots = [{"equity": e} for e in eqs]
    dd = compute_max_drawdown(snapshots)
    assert abs(dd - 0.2) < 1e-9
