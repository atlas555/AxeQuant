"""Fast ASR-Band backtest runner with optional Numba JIT acceleration.

Provides a numpy-array-based backtest loop that bypasses the dataclass/dict
overhead of the standard BacktestRunner.  When Numba is available, the inner
bar loop is JIT-compiled for 30-50x speedup on 15m data.

Falls back gracefully to pure-Python numpy if Numba is not installed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app.services.backtestsys_plugin.config.loader import BacktestConfig, load_config
from app.services.backtestsys_plugin.evaluation.metrics import MetricsCalculator, MetricsReport
from app.services.backtestsys_plugin.orchestrator.runner import BacktestResult, BARS_PER_YEAR
from app.services.backtestsys_plugin.signals.registry import SignalRegistry
from app.services.backtestsys_plugin.data_io.data_loader import DataLoader

# Auto-discover signal plugins
SignalRegistry.auto_discover()
import app.services.backtestsys_plugin.signals.technical.classic  # noqa: F401
import app.services.backtestsys_plugin.signals.technical.asrband  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Numba availability
# ---------------------------------------------------------------------------
HAS_NUMBA = False
try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Channel / signal array column indices (must match _prepare_arrays)
# ---------------------------------------------------------------------------
CH_ORANGE = 0
CH_BLUE = 1
CH_YELLOW = 2
CH_CYAN = 3
CH_OB_LO = 4
CH_BB_UP = 5
CH_TREND = 6

SIG_LONG1 = 0
SIG_LONG2 = 1
SIG_LONG3 = 2
SIG_LONG4 = 3
SIG_ALL_LONG_SL = 4
SIG_LONG1_TP = 5
SIG_LONG2_TP = 6
SIG_LONG3_TP = 7
SIG_LONG4_CLOSE = 8
SIG_SHORT1 = 9
SIG_SHORT2 = 10
SIG_SHORT3 = 11
SIG_ALL_SHORT_SL = 12
SIG_SHORT1_TP = 13
SIG_SHORT2_TP = 14
SIG_SHORT3_TP = 15


# ---------------------------------------------------------------------------
# Core loop (pure numpy — JIT-wrapped when Numba is available)
# ---------------------------------------------------------------------------

def _run_loop_python(
    close_arr, ch_arr, sig_arr, lookback, cap,
    fee_rate, spread,
    long_pos_frac, short_pos_frac,
    long_tp_fracs, short_tp_fracs,
    min_hold_bars, sl_bars_confirm,
):
    """Pure-Python numpy inner loop for ASR-Band long+short strategy."""
    n = len(close_arr)
    equity = np.empty(n - lookback, dtype=np.float64)
    cash = cap
    trade_pnls = np.empty(n * 2, dtype=np.float64)
    n_trades = 0

    # Position arrays: L1-L4 (indices 0-3), S1-S3 (indices 4-6)
    N_LEGS = 7
    pos_active = np.zeros(N_LEGS, dtype=np.int32)
    pos_qty = np.zeros(N_LEGS, dtype=np.float64)
    pos_entry = np.zeros(N_LEGS, dtype=np.float64)
    pos_margin = np.zeros(N_LEGS, dtype=np.float64)
    pos_ef = np.zeros(N_LEGS, dtype=np.float64)
    pos_bar = np.zeros(N_LEGS, dtype=np.int64)  # entry bar index

    spread_buy = 1.0 + spread / 10000.0
    spread_sell = 1.0 - spread / 10000.0

    bars_below_blue = 0
    bars_above_orange = 0

    for i in range(lookback, n):
        close = close_arr[i]
        orange = ch_arr[i, CH_ORANGE]
        blue = ch_arr[i, CH_BLUE]

        if np.isnan(orange):
            teq = cash
            for k in range(N_LEGS):
                if pos_active[k] == 1:
                    d = 1.0 if k < 4 else -1.0
                    teq += pos_margin[k] + (close - pos_entry[k]) * pos_qty[k] * d
            equity[i - lookback] = teq
            continue

        yellow = ch_arr[i, CH_YELLOW]
        cyan = ch_arr[i, CH_CYAN]
        ob_lo = ch_arr[i, CH_OB_LO]
        bb_up = ch_arr[i, CH_BB_UP]

        closed = np.zeros(N_LEGS, dtype=np.int32)

        # SL confirmation counters
        if close < blue:
            bars_below_blue += 1
        else:
            bars_below_blue = 0
        if close > orange:
            bars_above_orange += 1
        else:
            bars_above_orange = 0

        # --- Long SL ---
        if sig_arr[i, SIG_ALL_LONG_SL] > 0.5 and bars_below_blue >= sl_bars_confirm:
            for k in range(4):
                if pos_active[k] == 1 and closed[k] == 0:
                    fp = blue * spread_sell
                    fee = abs(fp * pos_qty[k]) * fee_rate
                    pnl = (fp - pos_entry[k]) * pos_qty[k] - fee - pos_ef[k]
                    cash += pos_margin[k] + pnl + pos_ef[k]
                    trade_pnls[n_trades] = pnl
                    n_trades += 1
                    pos_active[k] = 0
                    closed[k] = 1

        # --- Short SL ---
        if sig_arr[i, SIG_ALL_SHORT_SL] > 0.5 and bars_above_orange >= sl_bars_confirm:
            for k in range(4, N_LEGS):
                if pos_active[k] == 1 and closed[k] == 0:
                    fp = orange * spread_buy
                    fee = abs(fp * pos_qty[k]) * fee_rate
                    pnl = (pos_entry[k] - fp) * pos_qty[k] - fee - pos_ef[k]
                    cash += pos_margin[k] + pnl + pos_ef[k]
                    trade_pnls[n_trades] = pnl
                    n_trades += 1
                    pos_active[k] = 0
                    closed[k] = 1

        # --- Long TP ---
        # L1 -> yellow, L2 -> ob_lo, L3 -> orange, L4 -> close
        long_tp_lines = (yellow, ob_lo, orange, close)
        long_tp_sigs = (SIG_LONG1_TP, SIG_LONG2_TP, SIG_LONG3_TP, SIG_LONG4_CLOSE)
        for k in range(4):
            if sig_arr[i, long_tp_sigs[k]] > 0.5 and pos_active[k] == 1 and closed[k] == 0:
                if min_hold_bars > 0 and (i - pos_bar[k]) < min_hold_bars:
                    continue
                frac = min(long_tp_fracs[k], 1.0)
                fp = long_tp_lines[k] * spread_sell
                close_qty = pos_qty[k] * frac
                fee = abs(fp * close_qty) * fee_rate
                cm = pos_margin[k] * frac
                ce = pos_ef[k] * frac
                pnl = (fp - pos_entry[k]) * close_qty - fee - ce
                cash += cm + pnl + ce
                trade_pnls[n_trades] = pnl
                n_trades += 1
                if frac >= 1.0:
                    pos_active[k] = 0
                    closed[k] = 1
                else:
                    remain = 1.0 - frac
                    pos_qty[k] *= remain
                    pos_margin[k] *= remain
                    pos_ef[k] *= remain

        # --- Short TP ---
        # S1 -> cyan, S2 -> bb_up, S3 -> blue
        short_tp_lines = (cyan, bb_up, blue)
        short_tp_sigs = (SIG_SHORT1_TP, SIG_SHORT2_TP, SIG_SHORT3_TP)
        for k in range(3):
            ki = k + 4  # index in position arrays
            if sig_arr[i, short_tp_sigs[k]] > 0.5 and pos_active[ki] == 1 and closed[ki] == 0:
                if min_hold_bars > 0 and (i - pos_bar[ki]) < min_hold_bars:
                    continue
                frac = min(short_tp_fracs[k], 1.0)
                fp = short_tp_lines[k] * spread_buy
                close_qty = pos_qty[ki] * frac
                fee = abs(fp * close_qty) * fee_rate
                cm = pos_margin[ki] * frac
                ce = pos_ef[ki] * frac
                pnl = (pos_entry[ki] - fp) * close_qty - fee - ce
                cash += cm + pnl + ce
                trade_pnls[n_trades] = pnl
                n_trades += 1
                if frac >= 1.0:
                    pos_active[ki] = 0
                    closed[ki] = 1
                else:
                    remain = 1.0 - frac
                    pos_qty[ki] *= remain
                    pos_margin[ki] *= remain
                    pos_ef[ki] *= remain

        # --- Equity for sizing ---
        teq = cash
        for k in range(N_LEGS):
            if pos_active[k] == 1:
                d = 1.0 if k < 4 else -1.0
                teq += pos_margin[k] + (close - pos_entry[k]) * pos_qty[k] * d

        pt = 0
        if i > lookback:
            pt = int(ch_arr[i - 1, CH_TREND])

        # --- Long entries ---
        pv_long = teq * long_pos_frac
        long_entry_prices = (cyan, bb_up, blue, orange)
        long_sigs = (SIG_LONG1, SIG_LONG2, SIG_LONG3, SIG_LONG4)
        for k in range(4):
            ep = long_entry_prices[k]
            if (sig_arr[i, long_sigs[k]] > 0.5
                    and pos_active[k] == 0 and closed[k] == 0
                    and pt >= 1 and ep > 0):
                ep2 = ep * spread_buy
                qty = round(pv_long / ep2 * 10000.0) / 10000.0
                if qty > 0:
                    fee = abs(ep2 * qty) * fee_rate
                    margin = ep2 * qty
                    if margin + fee <= cash:
                        cash -= (margin + fee)
                        pos_active[k] = 1
                        pos_qty[k] = qty
                        pos_entry[k] = ep2
                        pos_margin[k] = margin
                        pos_ef[k] = fee
                        pos_bar[k] = i

        # --- Short entries ---
        pv_short = teq * short_pos_frac
        short_entry_prices = (yellow, ob_lo, orange)
        short_sigs = (SIG_SHORT1, SIG_SHORT2, SIG_SHORT3)
        for k in range(3):
            ki = k + 4
            ep = short_entry_prices[k]
            if (sig_arr[i, short_sigs[k]] > 0.5
                    and pos_active[ki] == 0 and closed[ki] == 0
                    and pt <= -1 and ep > 0):
                ep2 = ep * spread_sell
                qty = round(pv_short / ep2 * 10000.0) / 10000.0
                if qty > 0:
                    fee = abs(ep2 * qty) * fee_rate
                    margin = ep2 * qty
                    if margin + fee <= cash:
                        cash -= (margin + fee)
                        pos_active[ki] = 1
                        pos_qty[ki] = qty
                        pos_entry[ki] = ep2
                        pos_margin[ki] = margin
                        pos_ef[ki] = fee
                        pos_bar[ki] = i

        # --- Final equity ---
        teq = cash
        for k in range(N_LEGS):
            if pos_active[k] == 1:
                d = 1.0 if k < 4 else -1.0
                teq += pos_margin[k] + (close - pos_entry[k]) * pos_qty[k] * d
        equity[i - lookback] = teq

    return equity, trade_pnls[:n_trades]


# Create JIT version if available
if HAS_NUMBA:
    _run_loop_jit = njit(cache=True)(_run_loop_python)
else:
    _run_loop_jit = None


# ---------------------------------------------------------------------------
# Array preparation
# ---------------------------------------------------------------------------

def _prepare_arrays(data: pd.DataFrame, ch: pd.DataFrame):
    """Convert DataFrames to numpy arrays for the fast loop."""
    close_arr = data["close"].values.astype(np.float64)

    ch_arr = np.column_stack([
        ch["orange_line"].values.astype(np.float64),
        ch["blue_line"].values.astype(np.float64),
        ch["yellow_line"].values.astype(np.float64),
        ch["cyan_line"].values.astype(np.float64),
        ch["orange_band_lower"].values.astype(np.float64),
        ch["blue_band_upper"].values.astype(np.float64),
        ch["trend_state"].values.astype(np.float64),
    ])

    sig_cols = [
        "long1", "long2", "long3", "long4",
        "all_long_sl",
        "long1_tp", "long2_tp", "long3_tp", "long4_close",
        "short1", "short2", "short3",
        "all_short_sl",
        "short1_tp", "short2_tp", "short3_tp",
    ]
    sig_arr = np.column_stack([
        ch[col].values.astype(np.float64) if col in ch.columns
        else np.zeros(len(ch), dtype=np.float64)
        for col in sig_cols
    ])

    return close_arr, ch_arr, sig_arr


# ---------------------------------------------------------------------------
# FastBacktestRunner
# ---------------------------------------------------------------------------

class FastBacktestRunner:
    """High-performance ASR-Band backtest runner using numpy arrays.

    When Numba is installed, the inner bar loop is JIT-compiled.
    Otherwise falls back to pure-Python numpy (still faster than the
    standard event-driven runner due to no dataclass/dict overhead).
    """

    def run(self, config_path: str) -> BacktestResult:
        """Load a YAML config and run the fast backtest."""
        cfg = load_config(config_path)
        return self.run_config(cfg)

    def run_config(self, cfg: BacktestConfig) -> BacktestResult:
        """Run the fast backtest from a loaded config."""
        t0 = time.time()

        # 1. Load data
        loader = DataLoader(cfg.data.data_dir)
        data = loader.load(cfg.data.symbol, cfg.data.timeframe, cfg.data.start, cfg.data.end)
        if data.empty:
            raise ValueError(f"No data for {cfg.data.symbol} {cfg.data.timeframe}")

        # 2. Compute signals
        sig_params = {}
        for sig_name, sig_def in cfg.signals.items():
            if sig_def["type"] == "asrband":
                sig_params = sig_def.get("params", {})
                break
        signal = SignalRegistry.create("asrband", **sig_params)
        frame = signal.compute(data)
        ch = frame.metadata["channels"]
        lookback = signal.lookback

        # 3. Prepare numpy arrays
        close_arr, ch_arr, sig_arr = _prepare_arrays(data, ch)

        # 4. Extract config params
        strat = cfg.strategy
        long_pos_frac = getattr(strat, "pos_frac_long", 0.0) or 0.50
        short_pos_frac = getattr(strat, "pos_frac_short", 0.0) or 0.15
        fee_rate = cfg.execution.fees.maker
        spread = cfg.execution.slippage.spread_bps if cfg.execution.slippage.enabled else 3.0
        min_hold_bars = getattr(strat, "min_hold_bars", 0)
        sl_bars_confirm = getattr(strat, "sl_bars_confirm", 1)

        tp_fracs = strat.tp_fracs_by_level if hasattr(strat, "tp_fracs_by_level") else {}
        long_tp = np.array([
            tp_fracs.get("long1", 1.0),
            tp_fracs.get("long2", 1.0),
            tp_fracs.get("long3", 1.0),
            tp_fracs.get("long4", 1.0),
        ], dtype=np.float64)
        short_tp = np.array([
            tp_fracs.get("short1", 1.0),
            tp_fracs.get("short2", 1.0),
            tp_fracs.get("short3", 1.0),
        ], dtype=np.float64)

        # 5. Run loop
        run_fn = _run_loop_jit if _run_loop_jit is not None else _run_loop_python
        equity_arr, trade_pnls = run_fn(
            close_arr, ch_arr, sig_arr, lookback, cfg.backtest.initial_capital,
            fee_rate, spread,
            long_pos_frac, short_pos_frac,
            long_tp, short_tp,
            min_hold_bars, sl_bars_confirm,
        )

        elapsed = time.time() - t0
        logger.info("FastRunner: %d bars in %.2fs (Numba=%s)", len(data), elapsed, HAS_NUMBA)

        # 6. Build result
        bars_per_year = BARS_PER_YEAR.get(cfg.data.timeframe, 8760)
        trades = [
            SimpleNamespace(net_pnl=float(p), funding_pnl=0.0, is_liquidated=False)
            for p in trade_pnls
        ]
        metrics = MetricsCalculator.calculate_all(
            equity_arr, trades, risk_free_rate=0.0, bars_per_year=bars_per_year,
        )

        return BacktestResult(
            metrics=metrics,
            equity_curve=equity_arr.tolist(),
            trades=[],
            config=cfg,
            bars=data[["open", "high", "low", "close", "volume"]].copy() if "volume" in data.columns else data[["open", "high", "low", "close"]].copy(),
            equity_start_bar=lookback,
        )
