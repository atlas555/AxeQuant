"""Slippage model: Spread + Market Impact (Almgren-Chriss square root law).

From backtest.md S5.2:
  slipped_price = base_price + direction * (spread_cost + impact)
  spread_cost = base_price * spread_bps / 10000
  impact = impact_coeff * base_price * sqrt(quantity / bar_volume)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SlippageModel:
    """Simulates realistic execution slippage via half-spread + market impact.

    Parameters
    ----------
    spread_bps : float
        Half bid-ask spread in basis points (1 bp = 0.01%).
    impact_coeff : float
        Almgren-Chriss gamma coefficient for square-root market impact.
    enabled : bool
        When False, :meth:`apply` returns the base price unchanged.
    """

    spread_bps: float = 5.0
    impact_coeff: float = 0.1
    enabled: bool = True

    def apply(
        self,
        base_price: float,
        quantity: float,
        bar_volume: float,
        side: str,
    ) -> float:
        """Apply slippage to *base_price*.

        Buy orders: price moves UP (you pay more).
        Sell orders: price moves DOWN (you receive less).

        Parameters
        ----------
        base_price : float
            Raw execution price before slippage (typically bar.open).
        quantity : float
            Order size in base asset units.
        bar_volume : float
            Total volume of the bar (used for participation rate).
        side : str
            ``"buy"`` or ``"sell"``.

        Returns
        -------
        float
            Slipped execution price.
        """
        if not self.enabled:
            return base_price

        # 1. Spread cost (half the bid-ask spread)
        spread_cost = base_price * self.spread_bps / 10_000

        # 2. Market impact (Almgren-Chriss square root law)
        if bar_volume > 0 and quantity > 0:
            participation = quantity / bar_volume
            impact = self.impact_coeff * base_price * np.sqrt(participation)
        else:
            impact = 0.0

        # Direction: buy pushes price up, sell pushes down
        direction = 1.0 if side == "buy" else -1.0
        return base_price + direction * (spread_cost + impact)
