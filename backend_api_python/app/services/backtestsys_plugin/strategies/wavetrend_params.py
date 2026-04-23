"""WaveTrend strategy parameter definitions for the optimizer framework."""

from app.services.backtestsys_plugin.optimizer.param_spec import ParamSpec, StrategyParams

WAVETREND_SPECS = [
    # Layer 0: Signal (channel shape, smoothing — rarely tuned)
    ParamSpec("n1", 10, layer=0, bounds=(5, 20), step=1,
              description="Channel length for ESA and deviation"),
    ParamSpec("n2", 21, layer=0, bounds=(10, 42), step=3,
              description="Average length for wt1 EWM"),
    ParamSpec("smoothing", 4, layer=0, bounds=(0, 8), step=2,
              description="Extra EMA smoothing on CI (0=disabled)"),
    ParamSpec("ma_period", 4, layer=0, bounds=(2, 6), step=1,
              description="Rolling MA period for wt2"),

    # Layer 1: Structure (thresholds, hold — optimized first)
    ParamSpec("ob_level", 53, layer=1, bounds=(40, 70), step=5,
              description="Overbought level for cross_down filter"),
    ParamSpec("os_level", -53, layer=1, bounds=(-70, -40), step=5,
              description="Oversold level for cross_up filter"),
    ParamSpec("min_hold_bars", 0, layer=1, bounds=(0, 24), step=4,
              description="Min bars before exit allowed (0=disabled)"),

    # Layer 2: Sizing (optimized after structure is set)
    ParamSpec("pos_frac", 0.25, layer=2, bounds=(0.1, 0.5), step=0.05,
              description="Fraction of equity per position"),
]

WAVETREND_PARAMS = StrategyParams(WAVETREND_SPECS)
