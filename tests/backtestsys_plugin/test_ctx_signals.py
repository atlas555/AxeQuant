"""Tests for ctx.signal(...) adapter behavior."""

from __future__ import annotations


class _FakeCtx:
    """Minimal stand-in for StrategyScriptContext."""

    def __init__(self, df):
        self._bars_df = df
        self.current_index = 0


def test_attach_signals_adds_callable(sample_ohlcv):
    from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals

    ctx = _FakeCtx(sample_ohlcv)
    attach_signals(ctx)
    assert callable(ctx.signal)


def test_ctx_signal_returns_row_view(sample_ohlcv):
    from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals

    ctx = _FakeCtx(sample_ohlcv)
    attach_signals(ctx)
    ctx.current_index = 500

    row = ctx.signal("asrband", asr_length=50, ewm_halflife=80,
                     band_mult=0.22, channel_width=6.5)
    assert row is not None
    assert hasattr(row, "long1")
    assert hasattr(row, "short1")
    assert hasattr(row, "mid_line")
    assert isinstance(bool(row.long1), bool)


def test_ctx_signal_caches_per_params(sample_ohlcv):
    """Same name + params → same computed frame (memoization)."""
    from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals

    ctx = _FakeCtx(sample_ohlcv)
    attach_signals(ctx)
    ctx.current_index = 100

    params = dict(asr_length=50, ewm_halflife=80, band_mult=0.22, channel_width=6.5)
    proxy = ctx.signal
    proxy("asrband", **params)
    assert len(proxy._cache) == 1

    proxy("asrband", **params)  # same key
    assert len(proxy._cache) == 1

    proxy("asrband", asr_length=60, ewm_halflife=80, band_mult=0.22, channel_width=6.5)
    assert len(proxy._cache) == 2


def test_ctx_signal_unknown_field_raises(sample_ohlcv):
    from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals

    ctx = _FakeCtx(sample_ohlcv)
    attach_signals(ctx)
    ctx.current_index = 500
    row = ctx.signal("asrband", asr_length=50, ewm_halflife=80,
                     band_mult=0.22, channel_width=6.5)

    import pytest
    with pytest.raises(AttributeError):
        _ = row.nonexistent_field
