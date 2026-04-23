"""WaveTrend Regime-Adaptive parameter definitions for the optimizer."""

from app.services.backtestsys_plugin.optimizer.param_spec import ParamSpec, StrategyParams

WAVETREND_REGIME_SPECS = [
    # Layer 0: Signal (rarely tuned)
    ParamSpec("n1", 25, layer=0, bounds=(10, 30), step=5,
              description="WaveTrend channel length"),
    ParamSpec("n2", 42, layer=0, bounds=(20, 60), step=5,
              description="WaveTrend average length"),
    ParamSpec("smoothing", 4, layer=0, bounds=(0, 8), step=2,
              description="Extra EMA smoothing on CI"),
    ParamSpec("ma_period", 4, layer=0, bounds=(2, 6), step=1,
              description="SMA period for trigger line"),

    # Layer 1: Structure (optimized first)
    ParamSpec("ob_level", 75, layer=1, bounds=(50, 90), step=5,
              description="Overbought threshold"),
    ParamSpec("os_level", -40, layer=1, bounds=(-70, -30), step=5,
              description="Oversold threshold"),
    ParamSpec("adx_trending", 25, layer=1, bounds=(15, 35), step=5,
              description="ADX threshold for trending regime"),
    ParamSpec("min_hold_bars", 0, layer=1, bounds=(0, 32), step=8,
              description="Min bars before exit allowed"),
    ParamSpec("enable_trend_regime", True, layer=1, options=[True, False],
              description="Enable STRONG_TREND regime signals"),
    ParamSpec("enable_exhaust_regime", True, layer=1, options=[True, False],
              description="Enable EXHAUSTION regime signals"),
    ParamSpec("enable_accum_regime", True, layer=1, options=[True, False],
              description="Enable ACCUMULATION regime signals"),
    ParamSpec("enable_distrib_regime", True, layer=1, options=[True, False],
              description="Enable DISTRIBUTION regime signals"),

    # Layer 2: Sizing
    ParamSpec("pos_frac", 0.5, layer=1, bounds=(0.1, 0.5), step=0.05,
              description="Fraction of equity per position"),
]

WAVETREND_REGIME_PARAMS = StrategyParams(WAVETREND_REGIME_SPECS)
