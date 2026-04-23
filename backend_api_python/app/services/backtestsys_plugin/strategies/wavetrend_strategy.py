"""WaveTrend strategy — single-position long/short on OB/OS crosses.

Opens a long on cross_up, opens a short on cross_down.  Positions are
closed on the opposite cross signal or after min_hold_bars elapse.
"""

from __future__ import annotations

from app.services.backtestsys_plugin.core.types import Order, OrderSide, OrderType
from app.services.backtestsys_plugin.strategies.base import BarContext, Strategy
from app.services.backtestsys_plugin.strategies.registry import StrategyRegistry


@StrategyRegistry.register("wavetrend")
class WaveTrendStrategy(Strategy):
    """Simple single-position strategy driven by WaveTrend cross signals."""

    @classmethod
    def from_config(cls, cfg) -> "WaveTrendStrategy":
        return cls(
            pos_frac=getattr(cfg, "pos_frac", 0.25),
            min_hold_bars=getattr(cfg, "min_hold_bars", 0),
            leverage=getattr(cfg, "leverage", 1),
            risk_per_trade=getattr(cfg, "risk_per_trade", 0.01),
        )

    def __init__(
        self,
        pos_frac: float = 0.25,
        min_hold_bars: int = 0,
        leverage: int = 1,
        risk_per_trade: float = 0.01,
    ) -> None:
        self.pos_frac = pos_frac
        self.min_hold_bars = min_hold_bars
        self.leverage = leverage
        self.risk_per_trade = risk_per_trade

        # State: bar index when each leg was entered
        self._entry_bar: dict[str, int] = {}

    # ── main entry point ─────────────────────────────────────────────

    def on_bar(self, ctx: BarContext) -> list[Order]:
        orders: list[Order] = []

        cross_up = ctx.signals.get("cross_up", 0)
        cross_down = ctx.signals.get("cross_down", 0)

        long_key = f"{ctx.symbol}:wt_long"
        short_key = f"{ctx.symbol}:wt_short"

        has_long = ctx.portfolio.has_position(long_key)
        has_short = ctx.portfolio.has_position(short_key)

        # ── 1. Exit logic ────────────────────────────────────────────

        if has_long and cross_down:
            if self._hold_satisfied(long_key, ctx.bar_idx):
                pos = ctx.portfolio.get_position(long_key)
                orders.append(Order(
                    symbol=long_key,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                    price=ctx.bar.close,
                    leverage=self.leverage,
                    reduce_only=True,
                ))
                self._entry_bar.pop(long_key, None)
                has_long = False

        if has_short and cross_up:
            if self._hold_satisfied(short_key, ctx.bar_idx):
                pos = ctx.portfolio.get_position(short_key)
                orders.append(Order(
                    symbol=short_key,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                    price=ctx.bar.close,
                    leverage=self.leverage,
                    reduce_only=True,
                ))
                self._entry_bar.pop(short_key, None)
                has_short = False

        # ── 2. Entry logic ───────────────────────────────────────────

        if not has_long and cross_up:
            qty = self._calc_qty(ctx)
            if qty > 0:
                orders.append(Order(
                    symbol=long_key,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=qty,
                    price=ctx.bar.close,
                    leverage=self.leverage,
                ))
                self._entry_bar[long_key] = ctx.bar_idx

        if not has_short and cross_down:
            qty = self._calc_qty(ctx)
            if qty > 0:
                orders.append(Order(
                    symbol=short_key,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=qty,
                    price=ctx.bar.close,
                    leverage=self.leverage,
                ))
                self._entry_bar[short_key] = ctx.bar_idx

        return orders

    # ── helpers ────────────────────────────────────────────────────────

    def _hold_satisfied(self, pos_key: str, bar_idx: int) -> bool:
        """Return True when min_hold_bars has elapsed since entry."""
        if self.min_hold_bars <= 0:
            return True
        entry = self._entry_bar.get(pos_key, 0)
        return (bar_idx - entry) >= self.min_hold_bars

    def _calc_qty(self, ctx: BarContext) -> float:
        """Position size = equity * pos_frac / leverage / price."""
        price = ctx.bar.close
        if price <= 0:
            return 0.0
        return round(
            ctx.portfolio.total_equity * self.pos_frac / self.leverage / price,
            4,
        )
