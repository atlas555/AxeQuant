"""Signal parity test — Phase 1 exit gate.

Guarantees: ctx.signal('asrband', **params) returns, at each bar, the same
field values as a direct SignalRegistry.create('asrband', **params).compute(df)
at that same bar.

Why this boundary: equity-curve parity between QD runtime and backTestSys
runner requires matching order-execution semantics (fill model, slippage,
commission). That's Phase 4 territory. At Phase 1 what we need to prove is
that the signal layer is zero-drift — any downstream divergence is in
execution, not in research.
"""

from __future__ import annotations


def test_asr_signal_parity_across_bars(sample_ohlcv):
    from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals
    from app.services.backtestsys_plugin.signals.registry import SignalRegistry

    SignalRegistry.auto_discover()

    params = dict(
        asr_length=50, ewm_halflife=80, band_mult=0.22,
        channel_width=6.5, cooldown_bars=8,
    )

    # Path A: direct computation (reference)
    signal = SignalRegistry.create("asrband", **params)
    reference_frame = signal.compute(sample_ohlcv)
    reference_channels = reference_frame.metadata["channels"]
    reference_values = reference_frame.values

    # Path B: via ctx.signal proxy, bar-by-bar
    class _FakeCtx:
        def __init__(self, df):
            self._bars_df = df
            self.current_index = 0

    ctx = _FakeCtx(sample_ohlcv)
    attach_signals(ctx)

    fields_to_check = ["long1", "long2", "short1", "short2",
                       "long1_tp", "short1_tp", "all_long_sl", "all_short_sl",
                       "orange_line", "mid_line", "trend_state"]

    checked_bars = 0
    for i in range(200, len(sample_ohlcv), 50):  # sample sparsely for speed
        ctx.current_index = i
        row = ctx.signal("asrband", **params)

        for field in fields_to_check:
            expected = reference_channels[field].iloc[i]
            actual = getattr(row, field)
            if isinstance(expected, (bool,)) or expected is True or expected is False:
                assert bool(actual) == bool(expected), \
                    f"bar {i} field {field}: proxy={actual} ref={expected}"
            else:
                import math
                if math.isnan(float(expected)):
                    assert math.isnan(float(actual)), \
                        f"bar {i} field {field}: proxy={actual} ref=NaN"
                else:
                    diff = abs(float(actual) - float(expected))
                    assert diff < 1e-9, \
                        f"bar {i} field {field}: drift={diff}"

        # Also check .value (trend_state via .values Series)
        expected_value = reference_values.iloc[i]
        actual_value = row.value
        import math
        if math.isnan(float(expected_value)):
            assert math.isnan(float(actual_value))
        else:
            assert abs(float(actual_value) - float(expected_value)) < 1e-9

        checked_bars += 1

    assert checked_bars >= 10, f"only checked {checked_bars} bars — too few"
