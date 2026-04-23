"""WaveTrend + Order Flow parameter definitions for the optimizer framework."""

from app.services.backtestsys_plugin.optimizer.param_spec import ParamSpec, StrategyParams

WAVETREND_OF_SPECS = [
    # Layer 0: Signal (rarely tuned — fixed across most optimizations)
    ParamSpec("n1", 10, layer=0, bounds=(5, 20), step=1,
              description="WaveTrend channel length"),
    ParamSpec("n2", 21, layer=0, bounds=(10, 42), step=3,
              description="WaveTrend average length"),
    ParamSpec("smoothing", 4, layer=0, bounds=(0, 8), step=2,
              description="Extra EMA smoothing on CI"),
    ParamSpec("ma_period", 4, layer=0, bounds=(2, 6), step=1,
              description="SMA period for trigger line"),

    # Layer 1: Structure (optimized first — decides what trades to take)
    ParamSpec("ob_level", 53, layer=1, bounds=(40, 70), step=5,
              description="Overbought threshold for cross_down signals"),
    ParamSpec("os_level", -53, layer=1, bounds=(-70, -40), step=5,
              description="Oversold threshold for cross_up signals"),
    ParamSpec("min_hold_bars", 8, layer=1, bounds=(0, 24), step=4,
              description="Min bars before exit allowed (0=disabled)"),

    # Layer 1: Order Flow gates (each is a boolean toggle)
    ParamSpec("gate_cvd_divergence", False, layer=1,
              options=[True, False],
              description="Veto if CVD diverges against trade direction"),
    ParamSpec("gate_delta_confirm", False, layer=1,
              options=[True, False],
              description="Require per-bar delta to confirm direction"),
    ParamSpec("gate_mfi_confluence", False, layer=1,
              options=[True, False],
              description="Require MFI in same OB/OS zone as WaveTrend"),
    ParamSpec("gate_vwap_side", False, layer=1,
              options=[True, False],
              description="Require price on correct side of VWAP"),
    ParamSpec("gate_absorption", False, layer=1,
              options=[True, False],
              description="Require absorption candle at WT extreme"),
    ParamSpec("gate_volume_threshold", False, layer=1,
              options=[True, False],
              description="Require volume above 1.2x SMA"),
    ParamSpec("gate_volume_regime_adaptive", False, layer=1,
              options=[True, False],
              description="Reject signals in LOW volume regime"),

    # Layer 2: Sizing (optimized after structure is set)
    ParamSpec("pos_frac", 0.25, layer=2, bounds=(0.1, 0.5), step=0.05,
              description="Fraction of equity per position"),
]

WAVETREND_OF_PARAMS = StrategyParams(WAVETREND_OF_SPECS)
