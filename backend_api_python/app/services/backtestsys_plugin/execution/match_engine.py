"""Base matching engine with next-bar-open execution to prevent look-ahead bias."""

from __future__ import annotations

from app.services.backtestsys_plugin.core.types import Bar, Fill, Order, OrderSide, OrderType
from app.services.backtestsys_plugin.execution.fees import ExchangeFeeModel
from app.services.backtestsys_plugin.execution.slippage import SlippageModel


class MatchEngine:
    """Simulated matching engine for backtesting.

    Orders are submitted on bar *N* and executed at bar *N+1*'s open price,
    eliminating look-ahead bias.

    Parameters
    ----------
    fees : ExchangeFeeModel
        Fee calculator used for every fill.
    slippage : SlippageModel | None
        Optional slippage model applied to market order fills.
    """

    def __init__(self, fees: ExchangeFeeModel, slippage: SlippageModel | None = None) -> None:
        self.fees = fees
        self.slippage = slippage
        self.pending_orders: list[Order] = []

    def submit_order(self, order: Order, bar_idx: int, immediate: bool = False) -> None:
        """Queue *order* for execution.

        Parameters
        ----------
        order : Order
            The order to submit.
        bar_idx : int
            Current bar index.
        immediate : bool
            If True, execute on the *same* bar (touch-price mode).
            If False (default), execute on bar *bar_idx + 1* (next-bar-open).
        """
        order.submitted_at = bar_idx
        order.execute_at = bar_idx if immediate else bar_idx + 1
        self.pending_orders.append(order)

    def process_bar(self, bar_idx: int, bar: Bar, portfolio) -> list[Fill]:
        """Execute eligible MARKET orders at *bar*.open.

        Only orders whose ``execute_at == bar_idx`` are filled.  Executed
        orders are removed from the pending list.

        Returns
        -------
        list[Fill]
            Fills generated this bar (may be empty).
        """
        fills: list[Fill] = []
        remaining: list[Order] = []

        for order in self.pending_orders:
            if order.order_type == OrderType.MARKET and order.execute_at == bar_idx:
                if order.price is not None and order.price > 0:
                    fill_price = order.price  # touch-price execution
                else:
                    fill_price = bar.open
                if self.slippage:
                    side_str = "buy" if order.side == OrderSide.BUY else "sell"
                    fill_price = self.slippage.apply(
                        fill_price, order.quantity, bar.volume, side_str,
                    )
                fee = self.fees.calculate(fill_price, order.quantity, is_maker=False)
                fills.append(
                    Fill(
                        order=order,
                        fill_price=fill_price,
                        fill_quantity=order.quantity,
                        fee=fee,
                        bar_idx=bar_idx,
                    )
                )
            else:
                remaining.append(order)

        self.pending_orders = remaining
        return fills
