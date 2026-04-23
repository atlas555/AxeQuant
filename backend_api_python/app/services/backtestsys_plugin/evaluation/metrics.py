"""Performance metrics for backtest evaluation.

Provides :class:`MetricsReport` (immutable result) and
:class:`MetricsCalculator` (stateless computation).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from app.services.backtestsys_plugin.core.types import Trade


# ── Report ───────────────────────────────────────────────────────────

@dataclass
class MetricsReport:
    """Container for all backtest performance metrics."""

    # Return metrics
    total_return: float = 0.0
    annual_return: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Drawdown
    max_drawdown: float = 0.0
    max_dd_duration_bars: int = 0

    # Trade statistics
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # Crypto-specific
    total_funding_pnl: float = 0.0
    total_liquidations: int = 0

    def to_dict(self) -> dict:
        """Return all fields as a plain dict."""
        return asdict(self)


# ── Calculator ───────────────────────────────────────────────────────

class MetricsCalculator:
    """Stateless calculator — all methods are class-level."""

    @classmethod
    def calculate_all(
        cls,
        equity_curve: np.ndarray,
        trades: list[Trade],
        risk_free_rate: float = 0.0,
        bars_per_year: int = 8760,
    ) -> MetricsReport:
        """Compute all metrics from an equity curve and trade list.

        Parameters
        ----------
        equity_curve:
            1-D array of portfolio equity values (one per bar).
        trades:
            Closed trades produced by the backtest engine.
        risk_free_rate:
            Annualised risk-free rate (default 0 for crypto).
        bars_per_year:
            Number of bars in one year (default 8760 = hourly).
        """
        report = MetricsReport()
        eq = np.asarray(equity_curve, dtype=np.float64)

        if len(eq) < 2:
            return report

        # ── Returns ──────────────────────────────────────────────
        report.total_return = (eq[-1] / eq[0]) - 1.0

        n_bars = len(eq)
        years = n_bars / bars_per_year
        if years > 0 and report.total_return > -1.0:
            try:
                # Use math.exp/log to avoid numpy overflow on tiny years
                log_growth = math.log1p(report.total_return) / years
                report.annual_return = math.expm1(log_growth)
                if not math.isfinite(report.annual_return):
                    report.annual_return = 0.0
            except (OverflowError, ValueError):
                report.annual_return = 0.0
        else:
            report.annual_return = -1.0

        # ── Drawdown ────────────────────────────────────────────
        cummax = np.maximum.accumulate(eq)
        drawdowns = (eq - cummax) / cummax  # non-positive array
        report.max_drawdown = float(np.min(drawdowns))

        # Duration: bars from peak to trough of the worst drawdown
        if report.max_drawdown < 0.0:
            trough_idx = int(np.argmin(drawdowns))
            # Find the most recent peak before the trough
            peak_idx = int(np.argmax(eq[:trough_idx + 1]))
            report.max_dd_duration_bars = trough_idx - peak_idx
        else:
            report.max_dd_duration_bars = 0

        # ── Risk-adjusted ratios ────────────────────────────────
        bar_returns = np.diff(eq) / eq[:-1]
        rfr_per_bar = (1.0 + risk_free_rate) ** (1.0 / bars_per_year) - 1.0
        excess = bar_returns - rfr_per_bar

        std = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
        mean_excess = float(np.mean(excess))

        # Sharpe
        if std > 0.0:
            report.sharpe_ratio = (mean_excess / std) * np.sqrt(bars_per_year)
        else:
            report.sharpe_ratio = 0.0

        # Sortino (downside deviation)
        downside = np.minimum(excess, 0.0)
        downside_std = float(np.sqrt(np.mean(downside ** 2)))
        if downside_std > 0.0:
            report.sortino_ratio = (mean_excess / downside_std) * np.sqrt(bars_per_year)
        else:
            report.sortino_ratio = 0.0

        # Calmar
        if report.max_drawdown < 0.0:
            report.calmar_ratio = report.annual_return / abs(report.max_drawdown)
        else:
            report.calmar_ratio = 0.0

        # ── Trade statistics ────────────────────────────────────
        n_trades = len(trades)
        report.total_trades = n_trades

        if n_trades > 0:
            net_pnls = np.array([t.net_pnl for t in trades], dtype=np.float64)
            wins = net_pnls > 0.0
            report.win_rate = float(np.sum(wins)) / n_trades

            gross_profit = float(np.sum(net_pnls[wins]))
            gross_loss = float(np.sum(net_pnls[~wins]))

            if gross_loss < 0.0:
                report.profit_factor = gross_profit / abs(gross_loss)
            elif gross_profit > 0.0:
                report.profit_factor = float("inf")
            else:
                report.profit_factor = 0.0

            report.expectancy = float(np.mean(net_pnls))

        # ── Crypto-specific ─────────────────────────────────────
        report.total_funding_pnl = sum(t.funding_pnl for t in trades)
        report.total_liquidations = sum(1 for t in trades if t.is_liquidated)

        return report
