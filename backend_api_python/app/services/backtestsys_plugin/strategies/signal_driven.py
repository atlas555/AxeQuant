"""Signal-driven futures strategy with ATR-based position sizing.

Opens long/short positions when a named signal crosses a threshold,
and brackets each entry with a stop-loss and take-profit order.
"""

from __future__ import annotations

import math

from app.services.backtestsys_plugin.core.types import Order, OrderSide, OrderType, PositionSide
from app.services.backtestsys_plugin.strategies.base import BarContext, Strategy, StrategyConfig
from app.services.backtestsys_plugin.strategies.registry import StrategyRegistry


@StrategyRegistry.register("signal_driven_futures")
class SignalDrivenFuturesStrategy(Strategy):
    """Concrete strategy that reacts to pre-computed signals."""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    @classmethod
    def from_config(cls, cfg: StrategyConfig) -> "SignalDrivenFuturesStrategy":
        return cls(cfg)

    # ── main entry point ────────────────────────────────────────────

    def on_bar(self, ctx: BarContext) -> list[Order]:
        cfg = self.config

        # Skip if already in a position
        if ctx.portfolio.has_position(ctx.symbol):
            return []

        # Require ATR signal
        atr = ctx.signals.get("atr")
        if atr is None or math.isnan(atr):
            return []

        # Check long entry
        long_val = ctx.signals.get(cfg.entry_long_signal)
        if long_val is not None and long_val > cfg.entry_long_threshold:
            return self._open_long(ctx, atr)

        # Check short entry
        short_val = ctx.signals.get(cfg.entry_short_signal)
        if short_val is not None and short_val < -cfg.entry_short_threshold:
            return self._open_short(ctx, atr)

        return []

    # ── entry helpers ────────────────────────────────────────────────

    def _open_long(self, ctx: BarContext, atr: float) -> list[Order]:
        return self._create_bracket_orders(ctx, atr, side=PositionSide.LONG)

    def _open_short(self, ctx: BarContext, atr: float) -> list[Order]:
        return self._create_bracket_orders(ctx, atr, side=PositionSide.SHORT)

    def _create_bracket_orders(self, ctx: BarContext, atr: float, side: PositionSide) -> list[Order]:
        cfg = self.config
        close = ctx.bar.close
        stop_distance = atr * cfg.stop_loss_atr_mult
        qty = self._position_size(ctx.portfolio.total_equity, stop_distance)

        if side == PositionSide.LONG:
            entry_side, exit_side = OrderSide.BUY, OrderSide.SELL
            sl_price = close - stop_distance
            tp_price = close + stop_distance * cfg.take_profit_rr
        else:
            entry_side, exit_side = OrderSide.SELL, OrderSide.BUY
            sl_price = close + stop_distance
            tp_price = close - stop_distance * cfg.take_profit_rr

        return [
            Order(symbol=ctx.symbol, side=entry_side, order_type=OrderType.MARKET,
                  quantity=qty, leverage=cfg.leverage),
            Order(symbol=ctx.symbol, side=exit_side, order_type=OrderType.STOP_MARKET,
                  quantity=qty, price=sl_price, leverage=cfg.leverage, reduce_only=True),
            Order(symbol=ctx.symbol, side=exit_side, order_type=OrderType.TAKE_PROFIT,
                  quantity=qty, price=tp_price, leverage=cfg.leverage, reduce_only=True),
        ]

    # ── position sizing ─────────────────────────────────────────────

    def _position_size(self, equity: float, stop_distance: float) -> float:
        """Calculate quantity based on fixed-fraction risk.

        risk_amount = equity * risk_per_trade
        quantity    = risk_amount / stop_distance  (rounded to 4 dp)
        """
        risk_amount = equity * self.config.risk_per_trade
        return round(risk_amount / stop_distance, 4)
