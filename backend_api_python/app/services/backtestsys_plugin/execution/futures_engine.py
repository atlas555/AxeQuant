"""Futures matching engine with liquidation, stop/TP, and market order processing."""

from __future__ import annotations

from app.services.backtestsys_plugin.core.types import Bar, Fill, Order, OrderSide, OrderType
from app.services.backtestsys_plugin.execution.fees import ExchangeFeeModel
from app.services.backtestsys_plugin.execution.margin import MarginEngine
from app.services.backtestsys_plugin.execution.match_engine import MatchEngine


class FuturesMatchEngine(MatchEngine):
    """Matching engine for perpetual futures with three-phase bar processing.

    Processing order per bar (critical for correctness):
      1. **Liquidation** -- forced closes checked first via :class:`MarginEngine`.
      2. **Conditional orders** -- STOP_MARKET / TAKE_PROFIT triggered by H/L.
      3. **Market orders** -- executed at bar.open via :meth:`super().process_bar`.

    Parameters
    ----------
    fees : ExchangeFeeModel
        Fee calculator.
    margin : MarginEngine
        Margin / liquidation checker.
    """

    def __init__(self, fees: ExchangeFeeModel, margin: MarginEngine, slippage=None) -> None:
        super().__init__(fees, slippage=slippage)
        self.margin = margin

    # --------------------------------------------------------------------- #
    # Conditional order trigger logic
    # --------------------------------------------------------------------- #

    @staticmethod
    def _is_triggered(order: Order, bar: Bar) -> bool:
        """Return True if a conditional order is triggered by the bar's range.

        STOP_MARKET
          - Sell stop: bar.low <= order.price  (price falling to stop)
          - Buy stop:  bar.high >= order.price (price rising to stop)

        TAKE_PROFIT
          - Sell TP: bar.high >= order.price (price rising to target)
          - Buy TP:  bar.low <= order.price  (price falling to target)
        """
        if order.order_type == OrderType.STOP_MARKET:
            if order.side == OrderSide.SELL:
                return bar.low <= order.price
            else:
                return bar.high >= order.price

        if order.order_type == OrderType.TAKE_PROFIT:
            if order.side == OrderSide.SELL:
                return bar.high >= order.price
            else:
                return bar.low <= order.price

        return False

    # --------------------------------------------------------------------- #
    # Main bar processing
    # --------------------------------------------------------------------- #

    def process_bar(self, bar_idx: int, bar: Bar, portfolio) -> list[Fill]:
        """Process a single bar in the correct priority order.

        Returns all fills (liquidation + conditional + market) for this bar.
        """
        all_fills: list[Fill] = []

        # ── Phase 1: liquidation check ──────────────────────────────
        liq_fills = self.margin.check_liquidation(bar, portfolio)
        for f in liq_fills:
            f.bar_idx = bar_idx
        all_fills.extend(liq_fills)

        # ── Phase 2: conditional orders (STOP_MARKET / TAKE_PROFIT) ─
        remaining: list[Order] = []
        for order in self.pending_orders:
            if order.order_type in (OrderType.STOP_MARKET, OrderType.TAKE_PROFIT):
                if self._is_triggered(order, bar):
                    fee = self.fees.calculate(
                        order.price, order.quantity, is_maker=False,
                    )
                    all_fills.append(
                        Fill(
                            order=order,
                            fill_price=order.price,
                            fill_quantity=order.quantity,
                            fee=fee,
                            bar_idx=bar_idx,
                        )
                    )
                else:
                    remaining.append(order)
            else:
                remaining.append(order)

        self.pending_orders = remaining

        # ── Phase 3: market orders via base class ───────────────────
        market_fills = super().process_bar(bar_idx, bar, portfolio)
        all_fills.extend(market_fills)

        return all_fills
