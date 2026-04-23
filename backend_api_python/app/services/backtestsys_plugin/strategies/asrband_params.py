"""ASR-Band strategy parameter definitions for the optimizer framework."""
from app.services.backtestsys_plugin.optimizer.param_spec import ParamSpec, StrategyParams

ASRBAND_SPECS = [
    # Layer 0: Signal (rarely tuned — fixed across most optimizations)
    ParamSpec("asr_length", 110, layer=0, bounds=(50, 200), step=10,
              description="SMA lookback for channel center"),
    ParamSpec("channel_width", 8.0, layer=0, bounds=(5.0, 12.0), step=0.5,
              description="VoV multiplier for channel distance"),
    ParamSpec("ewm_halflife", 205, layer=0, bounds=(100, 300), step=5,
              description="EWM halflife for VoV smoothing"),
    ParamSpec("band_mult", 0.09, layer=0, bounds=(0.05, 0.30), step=0.01,
              description="Band offset around channel lines"),
    ParamSpec("cooldown_bars", 10, layer=0, bounds=(0, 30), step=5,
              description="Min bars between signals per level"),

    # Layer 1: Structure (optimized first — decides what trades to take)
    ParamSpec("long_enabled", True, layer=1, options=[True, False],
              description="Enable long entries"),
    ParamSpec("short_enabled", True, layer=1, options=[True, False],
              description="Enable short entries"),
    ParamSpec("long_levels", [1, 2, 3, 4], layer=1,
              options=[[1, 2, 3, 4], [2, 3, 4], [1, 3, 4], [1, 2, 3]],
              description="Which long levels to trade"),
    ParamSpec("short_levels", [1, 2, 3], layer=1,
              options=[[1, 2, 3], [2, 3], [1, 2]],
              description="Which short levels to trade"),
    ParamSpec("min_hold_bars", 32, layer=1, bounds=(0, 96), step=8,
              description="Min bars before TP allowed (0=disabled)"),
    ParamSpec("sl_bars_confirm", 1, layer=1, bounds=(1, 4), step=1,
              description="Consecutive bars below SL line before triggering"),
    ParamSpec("compress_l1", False, layer=1, options=[True, False], danger=True,
              description="Require strong trend for L1/S1 (historically hurts)"),

    # Layer 2: Sizing (optimized after structure is set)
    ParamSpec("long_pos_frac", 0.55, layer=2, bounds=(0.1, 0.6), step=0.05,
              description="Fraction of equity per long position"),
    ParamSpec("short_pos_frac", 0.15, layer=2, bounds=(0.05, 0.3), step=0.05,
              description="Fraction of equity per short position"),
    ParamSpec("long_tp_L1", 0.70, layer=2, bounds=(0.1, 1.0), step=0.05,
              description="L1 take-profit fraction"),
    ParamSpec("long_tp_L2", 1.0, layer=2, bounds=(0.1, 1.0), step=0.05,
              description="L2 take-profit fraction"),
    ParamSpec("long_tp_L3", 1.0, layer=2, bounds=(0.1, 1.0), step=0.05,
              description="L3 take-profit fraction"),
    ParamSpec("long_tp_L4", 1.0, layer=2, bounds=(0.1, 1.0), step=0.05,
              description="L4 take-profit fraction"),
    ParamSpec("short_tp_S1", 0.70, layer=2, bounds=(0.1, 1.0), step=0.05,
              description="S1 take-profit fraction"),
    ParamSpec("short_tp_S2", 0.55, layer=2, bounds=(0.1, 1.0), step=0.05,
              description="S2 take-profit fraction"),
    ParamSpec("short_tp_S3", 0.80, layer=2, bounds=(0.1, 1.0), step=0.05,
              description="S3 take-profit fraction"),
]

ASRBAND_PARAMS = StrategyParams(ASRBAND_SPECS)
