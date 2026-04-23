"""Exchange fee model for perpetual futures backtesting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExchangeFeeModel:
    """Calculate trading fees for maker/taker orders.

    Default rates match typical Binance USDT-M futures VIP-0 tier.
    """

    maker: float = 0.0002
    taker: float = 0.0005

    def calculate(self, price: float, quantity: float, is_maker: bool) -> float:
        """Return the fee in quote currency for a fill.

        Parameters
        ----------
        price : float
            Fill price.
        quantity : float
            Fill quantity (sign ignored).
        is_maker : bool
            True for limit fills (maker rate), False for market/stop (taker).
        """
        rate = self.maker if is_maker else self.taker
        return abs(price * quantity) * rate
