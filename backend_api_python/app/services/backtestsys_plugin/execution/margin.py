"""Margin and liquidation engine for perpetual futures backtesting."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.backtestsys_plugin.core.types import Bar, Fill, Order, OrderSide, OrderType, Position, PositionSide


@dataclass
class MarginEngine:
    """Isolated-margin liquidation engine.

    Parameters
    ----------
    maintenance_rate : float
        Maintenance margin rate (default 0.004 = 0.4 %).
    """

    maintenance_rate: float = 0.004

    def calc_liquidation_price(self, pos: Position) -> float:
        """Return the liquidation price for *pos*.

        For long:  entry * (1 - 1/leverage + maintenance_rate)
        For short: entry * (1 + 1/leverage - maintenance_rate)
        """
        if pos.side == PositionSide.LONG:
            return pos.entry_price * (1 - 1 / pos.leverage + self.maintenance_rate)
        else:
            return pos.entry_price * (1 + 1 / pos.leverage - self.maintenance_rate)

    def check_liquidation(self, bar: Bar, portfolio) -> list[Fill]:
        """Check every position in *portfolio* against the bar's H/L range.

        Returns a list of liquidation :class:`Fill` objects (may be empty).
        """
        fills: list[Fill] = []
        for symbol, pos in portfolio.positions.items():
            liq_price = self.calc_liquidation_price(pos)

            triggered = False
            if pos.side == PositionSide.LONG and bar.low <= liq_price:
                triggered = True
            elif pos.side == PositionSide.SHORT and bar.high >= liq_price:
                triggered = True

            if triggered:
                close_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
                order = Order(
                    symbol=symbol,
                    side=close_side,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                    leverage=pos.leverage,
                    reduce_only=True,
                )
                fills.append(
                    Fill(
                        order=order,
                        fill_price=liq_price,
                        fill_quantity=pos.quantity,
                        fee=0.0,
                        bar_idx=0,
                        is_liquidation=True,
                    )
                )
        return fills
