"""Funding rate settlement engine for perpetual futures."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from app.services.backtestsys_plugin.core.types import Bar, Fill, Order, OrderSide, OrderType

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.backtestsys_plugin.core.portfolio import Portfolio

SETTLEMENT_HOURS_UTC = frozenset({0, 8, 16})


@dataclass
class FundingRateEngine:
    rates: pd.Series  # DatetimeIndex(UTC) -> funding rate

    def settle(self, bar: Bar, portfolio) -> list[Fill]:
        if bar.timestamp.hour not in SETTLEMENT_HOURS_UTC or bar.timestamp.minute != 0:
            return []
        rate = self._get_rate(bar.timestamp)
        if rate is None:
            return []
        fills = []
        for symbol, pos in portfolio.positions.items():
            pnl = -pos.notional_value * rate * pos.direction_sign
            dummy_order = Order(
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=0,
            )
            fills.append(
                Fill(
                    order=dummy_order,
                    fill_price=0.0,
                    fill_quantity=0.0,
                    fee=0.0,
                    bar_idx=0,
                    is_funding=True,
                    funding_pnl=pnl,
                )
            )
        return fills

    def _get_rate(self, timestamp: pd.Timestamp):
        if self.rates is None or len(self.rates) == 0:
            return None
        try:
            idx = self.rates.index.asof(timestamp)
            if pd.isna(idx):
                return None
            return float(self.rates[idx])
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Funding rate lookup failed for %s: %s", timestamp, e)
            return None
