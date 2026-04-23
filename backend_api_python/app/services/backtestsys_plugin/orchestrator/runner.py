"""End-to-end backtest orchestrator wiring Data, Signal, Strategy, Execution, and Evaluation layers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.services.backtestsys_plugin.config.loader import BacktestConfig, load_config
from app.services.backtestsys_plugin.core.portfolio import Portfolio
from app.services.backtestsys_plugin.core.types import Bar
from app.services.backtestsys_plugin.evaluation.metrics import MetricsCalculator, MetricsReport
from app.services.backtestsys_plugin.execution.fees import ExchangeFeeModel
from app.services.backtestsys_plugin.execution.futures_engine import FuturesMatchEngine
from app.services.backtestsys_plugin.execution.margin import MarginEngine
from app.services.backtestsys_plugin.execution.slippage import SlippageModel
from app.services.backtestsys_plugin.signals.registry import SignalRegistry
from app.services.backtestsys_plugin.strategies.base import BarContext, Strategy
from app.services.backtestsys_plugin.strategies.registry import StrategyRegistry
from app.services.backtestsys_plugin.data_io.data_loader import DataLoader
from app.services.backtestsys_plugin.data_io.validator import DataValidator

# Auto-discover signal and strategy plugins via decorators.
SignalRegistry.auto_discover()
import app.services.backtestsys_plugin.strategies.signal_driven  # noqa: F401
import app.services.backtestsys_plugin.strategies.asrband_strategy  # noqa: F401
import app.services.backtestsys_plugin.strategies.rebalance  # noqa: F401
import app.services.backtestsys_plugin.strategies.wavetrend_strategy  # noqa: F401
import app.services.backtestsys_plugin.strategies.wavetrend_of_strategy  # noqa: F401
import app.services.backtestsys_plugin.strategies.wavetrend_regime_strategy  # noqa: F401

logger = logging.getLogger(__name__)

# ── Bars-per-year mapping ──────────────────────────────────────────

BARS_PER_YEAR = {
    "1m": 525_600,
    "5m": 105_120,
    "15m": 35_040,
    "1h": 8_760,
    "4h": 2_190,
    "1d": 365,
}


# ── Result container ───────────────────────────────────────────────

@dataclass
class BacktestResult:
    metrics: MetricsReport
    equity_curve: list[float]
    trades: list
    config: BacktestConfig
    bars: pd.DataFrame | None = None
    equity_start_bar: int = 0


# ── Runner ─────────────────────────────────────────────────────────

class BacktestRunner:
    """Orchestrates the full backtest pipeline from config to metrics."""

    def run(self, config_path: str) -> BacktestResult:
        """Load a YAML config and run the backtest end-to-end."""
        cfg = load_config(config_path)
        return self.run_config(cfg)

    def run_config(self, cfg: BacktestConfig) -> BacktestResult:
        """Run a full backtest from an already-loaded config object."""
        data = self._load_data(cfg)
        return self.run_with_data(cfg, data)

    def run_with_data(self, cfg: BacktestConfig, data: pd.DataFrame) -> BacktestResult:
        """Run backtest on pre-loaded data (bypasses DataLoader).

        Use this when you already have a correctly-sliced DataFrame,
        e.g. from CPCV index-based splitting with purge/embargo gaps
        properly removed.
        """
        signal_arrays, max_lookback = self._compute_signals(cfg, data)
        strategy, engine, portfolio = self._setup_engine(cfg)
        self._run_loop(cfg, data, signal_arrays, max_lookback, strategy, engine, portfolio)
        result = self._evaluate(cfg, portfolio, data, max_lookback)
        if cfg.defense.trial_logger.enabled:
            from app.services.backtestsys_plugin.defense.trial_logger import TrialLogger
            TrialLogger(cfg.defense.trial_logger.registry).log(cfg.model_dump(), result.metrics)
        return result

    # ── Pipeline stages ────────────────────────────────────────────

    def _load_data(self, cfg: BacktestConfig) -> pd.DataFrame:
        """Stage 1: Load and validate OHLCV data."""
        loader = DataLoader(cfg.data.data_dir)
        data = loader.load(
            symbol=cfg.data.symbol,
            timeframe=cfg.data.timeframe,
            start=cfg.data.start,
            end=cfg.data.end,
        )

        if data.empty:
            raise ValueError(
                f"No data loaded for {cfg.data.symbol} / {cfg.data.timeframe} "
                f"[{cfg.data.start} .. {cfg.data.end}]"
            )

        validator = DataValidator()
        report = validator.validate(data, cfg.data.symbol)
        if not report.passed:
            logger.warning("Data validation issues: %s", report.summary)

        return data

    def _compute_signals(
        self, cfg: BacktestConfig, data: pd.DataFrame
    ) -> tuple[dict[str, np.ndarray], int]:
        """Stage 2: Vectorized signal pre-computation."""
        signal_frames: dict[str, np.ndarray] = {}
        max_lookback = 0
        computed_frames: dict = {}  # store frames for sub-signal extraction
        all_frames: list[tuple[str, object]] = []  # (sig_name, SignalFrame)

        for sig_name, sig_def in cfg.signals.items():
            sig_type = sig_def["type"]
            sig_params = sig_def.get("params", {})
            signal = SignalRegistry.create(sig_type, **sig_params)
            frame = signal.compute(data)
            signal_frames[sig_name] = frame.values.values  # pd.Series -> np.ndarray
            max_lookback = max(max_lookback, signal.lookback)
            all_frames.append((sig_name, frame))
            if frame.metadata.get("channels") is not None:
                computed_frames[sig_name] = frame

        # Composite signal: sma_cross = sma_fast - sma_slow
        if "sma_fast" in signal_frames and "sma_slow" in signal_frames:
            signal_frames["sma_cross"] = (
                signal_frames["sma_fast"] - signal_frames["sma_slow"]
            )

        # Extract sub-signals from ASR-Band channel metadata
        for name, frame in computed_frames.items():
            channels = frame.metadata["channels"]
            for col in channels.columns:
                if col.startswith(("long", "short", "all_", "trend",
                                    "orange_", "blue_", "yellow_", "cyan_", "mid_")):
                    signal_frames[col] = channels[col].values.astype(float)

        # Extract generic Series/ndarray metadata (WaveTrend, order flow, etc.)
        for sig_name, frame in all_frames:
            for key, val in frame.metadata.items():
                if key == "channels":
                    continue  # already handled above
                if isinstance(val, pd.Series):
                    signal_frames[key] = val.values.astype(float)
                elif isinstance(val, np.ndarray):
                    signal_frames[key] = val.astype(float)

        return signal_frames, max_lookback

    def _setup_engine(
        self, cfg: BacktestConfig
    ) -> tuple[Strategy, FuturesMatchEngine, Portfolio]:
        """Stage 3: Instantiate strategy, execution engine, and portfolio."""
        strategy = StrategyRegistry.create_from_config(cfg.strategy.type, cfg.strategy)

        fees = ExchangeFeeModel(
            maker=cfg.execution.fees.maker,
            taker=cfg.execution.fees.taker,
        )
        margin = MarginEngine(
            maintenance_rate=cfg.execution.margin.maintenance_rate,
        )
        slippage = None
        if cfg.execution.slippage.enabled:
            slippage = SlippageModel(
                spread_bps=cfg.execution.slippage.spread_bps,
                impact_coeff=cfg.execution.slippage.impact_coeff,
            )
        engine = FuturesMatchEngine(fees=fees, margin=margin, slippage=slippage)

        portfolio = Portfolio(initial_capital=cfg.backtest.initial_capital)

        return strategy, engine, portfolio

    def _run_loop(
        self,
        cfg: BacktestConfig,
        data: pd.DataFrame,
        signal_arrays: dict[str, np.ndarray],
        max_lookback: int,
        strategy: Strategy,
        engine: FuturesMatchEngine,
        portfolio: Portfolio,
    ) -> None:
        """Stage 4: Bar-by-bar event loop."""
        symbol = cfg.data.symbol
        n_bars = len(data)
        for bar_idx in range(max_lookback, n_bars):
            row = data.iloc[bar_idx]
            bar = Bar.from_series(row)

            # 4a. Process pending orders -> fills
            fills = engine.process_bar(bar_idx, bar, portfolio)

            # 4b. Apply fills to portfolio (skip stale close fills)
            for f in fills:
                try:
                    if (f.order.reduce_only or f.is_liquidation) and f.order.symbol not in portfolio.positions:
                        continue  # position already closed by earlier fill
                    portfolio.apply_fill(f)
                except Exception:
                    logger.exception("Fill processing error at bar %d, skipping fill", bar_idx)

            # 4b'. Cancel stale reduce_only orders for closed positions.
            # Works for both simple keys ("BTCUSDT:USDT") and compound
            # multi-leg keys ("BTCUSDT:USDT:long1").
            engine.pending_orders = [
                o for o in engine.pending_orders
                if not (o.reduce_only and o.symbol not in portfolio.positions)
            ]

            # 4c. Mark to market
            portfolio.mark_to_market({symbol: bar})

            # 4d. Record equity
            portfolio.record_equity()

            # 4e. Build signal snapshot for this bar
            current_signals: dict[str, float] = {}
            for sig_name, values in signal_arrays.items():
                current_signals[sig_name] = float(values[bar_idx])

            # 4f. Strategy decision
            ctx = BarContext(
                bar_idx=bar_idx,
                bar=bar,
                signals=current_signals,
                portfolio=portfolio.snapshot(),
                symbol=symbol,
            )
            try:
                orders = strategy.on_bar(ctx)
            except Exception:
                logger.exception("Strategy error at bar %d, skipping", bar_idx)
                orders = []

            # 4g. Submit orders
            immediate = (cfg.execution.match_mode == "touch_price")
            for order in orders:
                engine.submit_order(order, bar_idx, immediate=immediate)

            # 4h. Touch-price mode: process immediate orders on the same bar
            if immediate and engine.pending_orders:
                touch_fills = engine.process_bar(bar_idx, bar, portfolio)
                for f in touch_fills:
                    try:
                        if (f.order.reduce_only or f.is_liquidation) and f.order.symbol not in portfolio.positions:
                            continue
                        portfolio.apply_fill(f)
                    except Exception:
                        logger.exception("Touch fill error at bar %d", bar_idx)
                engine.pending_orders = [
                    o for o in engine.pending_orders
                    if not (o.reduce_only and o.symbol not in portfolio.positions)
                ]

    def _evaluate(
        self,
        cfg: BacktestConfig,
        portfolio: Portfolio,
        data: pd.DataFrame,
        max_lookback: int,
    ) -> BacktestResult:
        """Stage 5: Calculate metrics and build result container."""
        bars_per_year = self._bars_per_year(cfg.data.timeframe)
        metrics = MetricsCalculator().calculate_all(
            np.array(portfolio.equity_curve),
            portfolio.trade_log,
            risk_free_rate=cfg.evaluation.risk_free_rate,
            bars_per_year=bars_per_year,
        )

        return BacktestResult(
            metrics=metrics,
            equity_curve=portfolio.equity_curve,
            trades=portfolio.trade_log,
            config=cfg,
            bars=data.loc[:, [c for c in ["open", "high", "low", "close", "volume"] if c in data.columns]].copy(),
            equity_start_bar=max_lookback,
        )

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _bars_per_year(timeframe: str) -> int:
        return BARS_PER_YEAR.get(timeframe, 8760)
