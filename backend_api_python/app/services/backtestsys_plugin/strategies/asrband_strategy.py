"""ASR-Band strategy — multi-leg positions with channel-based exits.

Maps long1..long4 / short1..short4 entry signals to independent position legs
keyed as ``"{symbol}:long1"`` etc.  Exit timing is driven entirely by the signal
engine's TP/SL/close signals (channel-based), not by ATR bracket orders.

Signal priority within a single bar is handled upstream by the signal engine.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Iterable

from app.services.backtestsys_plugin.core.types import Order, OrderSide, OrderType
from app.services.backtestsys_plugin.strategies.base import BarContext, Strategy
from app.services.backtestsys_plugin.strategies.registry import StrategyRegistry

# Channel line mappings for touch-price execution.
# Entry: which channel line triggers the entry for each level.
_LONG_ENTRY_LINE = {
    "long1": "cyan_line",
    "long2": "blue_band_upper",
    "long3": "blue_line",
    "long4": "orange_line",
}
_SHORT_ENTRY_LINE = {
    "short1": "yellow_line",
    "short2": "orange_band_lower",
    "short3": "orange_line",
    "short4": "blue_line",
}
# Exit (TP): which channel line is the take-profit target.
_LONG_TP_LINE = {
    "long1": "yellow_line",
    "long2": "orange_band_lower",
    "long3": "orange_line",
}
_SHORT_TP_LINE = {
    "short1": "cyan_line",
    "short2": "blue_band_upper",
    "short3": "blue_line",
}
# SL lines
_LONG_SL_LINE = "blue_line"
_SHORT_SL_LINE = "orange_line"

# Level definitions
_PULLBACK_LONG_LEVELS = ("long1", "long2", "long3")
_PULLBACK_SHORT_LEVELS = ("short1", "short2", "short3")
_BREAKOUT_LONG_LEVEL = "long4"
_BREAKOUT_SHORT_LEVEL = "short4"

# Mapping: level -> its TP/close signal name
_LONG_TP = {
    "long1": "long1_tp",
    "long2": "long2_tp",
    "long3": "long3_tp",
    "long4": "long4_close",
}
_SHORT_TP = {
    "short1": "short1_tp",
    "short2": "short2_tp",
    "short3": "short3_tp",
    "short4": "short4_close",
}


@StrategyRegistry.register("asrband")
class AsrBandStrategy(Strategy):
    """Multi-leg strategy driven by ASR-Band channel signals.

    Each level (L1-L4, S1-S4) is an independent position leg that can coexist.
    Exits are determined by the signal engine's channel-based TP/SL signals
    rather than fixed ATR bracket orders.
    """

    @classmethod
    def from_config(cls, cfg) -> "AsrBandStrategy":
        return cls(
            leverage=cfg.leverage,
            risk_pct=cfg.risk_per_trade,
            atr_sl_mult=cfg.stop_loss_atr_mult,
            tp_rr=cfg.take_profit_rr,
            enable_breakout_levels=cfg.enable_breakout_levels,
            enable_short3_level=getattr(cfg, "enable_short3_level", True),
            allowed_entry_hours_utc=getattr(cfg, "allowed_entry_hours_utc", []),
            allowed_long_entry_hours_utc=getattr(cfg, "allowed_long_entry_hours_utc", []),
            allowed_short_entry_hours_utc=getattr(cfg, "allowed_short_entry_hours_utc", []),
            enabled_long_levels=getattr(cfg, "enabled_long_levels", []),
            enabled_short_levels=getattr(cfg, "enabled_short_levels", []),
            tp_fracs_by_level=getattr(cfg, "tp_fracs_by_level", {}),
            leg_weights_by_level=getattr(cfg, "leg_weights_by_level", {}),
            min_hold_bars=getattr(cfg, "min_hold_bars", 0),
            sl_bars_confirm=getattr(cfg, "sl_bars_confirm", 1),
            pos_frac_long=getattr(cfg, "pos_frac_long", 0.0),
            pos_frac_short=getattr(cfg, "pos_frac_short", 0.0),
        )

    def __init__(
        self,
        leverage: int = 3,
        risk_pct: float = 0.01,
        atr_sl_mult: float = 2.0,
        tp_rr: float = 1.5,  # kept for API compat; unused in channel mode
        enable_breakout_levels: bool = True,
        enable_short3_level: bool = True,
        allowed_entry_hours_utc: Iterable[int] | None = None,
        allowed_long_entry_hours_utc: Iterable[int] | None = None,
        allowed_short_entry_hours_utc: Iterable[int] | None = None,
        enabled_long_levels: Iterable[str] | None = None,
        enabled_short_levels: Iterable[str] | None = None,
        tp_fracs_by_level: Mapping[str, float] | None = None,
        leg_weights_by_level: Mapping[str, float] | None = None,
        min_hold_bars: int = 0,
        sl_bars_confirm: int = 1,
        pos_frac_long: float = 0.0,
        pos_frac_short: float = 0.0,
    ) -> None:
        self.leverage = leverage
        self.risk_pct = risk_pct
        self.atr_sl_mult = atr_sl_mult
        self.tp_rr = tp_rr
        self.enable_breakout_levels = enable_breakout_levels
        self.enable_short3_level = enable_short3_level
        self.allowed_entry_hours_utc = self._normalize_hours(allowed_entry_hours_utc)
        self.allowed_long_entry_hours_utc = self._normalize_hours(allowed_long_entry_hours_utc)
        self.allowed_short_entry_hours_utc = self._normalize_hours(allowed_short_entry_hours_utc)
        self.enabled_long_levels = self._normalize_levels(
            enabled_long_levels,
            _PULLBACK_LONG_LEVELS + (_BREAKOUT_LONG_LEVEL,),
        )
        self.enabled_short_levels = self._normalize_levels(
            enabled_short_levels,
            _PULLBACK_SHORT_LEVELS + (_BREAKOUT_SHORT_LEVEL,),
        )
        self.tp_fracs_by_level = dict(tp_fracs_by_level or {})
        self.leg_weights_by_level = dict(leg_weights_by_level or {})
        self.min_hold_bars = min_hold_bars
        self.sl_bars_confirm = max(sl_bars_confirm, 1)
        self.pos_frac_long = pos_frac_long
        self.pos_frac_short = pos_frac_short

        # State tracking for min_hold and SL confirmation
        self._entry_bar: dict[str, int] = {}
        self._bars_below_blue: int = 0
        self._bars_above_orange: int = 0

    # ── main entry point ─────────────────────────────────────────────

    def on_bar(self, ctx: BarContext) -> list[Order]:
        orders: list[Order] = []

        # Require ATR for position sizing
        atr = ctx.signals.get("atr")
        if atr is None or math.isnan(atr) or atr <= 0:
            return []

        # ── 0. Update SL confirmation counters ──────────────────────
        close_val = ctx.bar.close
        blue_line = ctx.signals.get("blue_line")
        orange_line = ctx.signals.get("orange_line")

        if blue_line is not None and not math.isnan(blue_line) and close_val < blue_line:
            self._bars_below_blue += 1
        else:
            self._bars_below_blue = 0

        if orange_line is not None and not math.isnan(orange_line) and close_val > orange_line:
            self._bars_above_orange += 1
        else:
            self._bars_above_orange = 0

        # ── 1. Exit signals ──────────────────────────────────────────

        # ALL_LONG_SL: close L1-L3 (L4 has its own close signal)
        if (ctx.signals.get("all_long_sl", 0) >= 1.0
                and self._bars_below_blue >= self.sl_bars_confirm):
            sl_price = self._touch_price(ctx, _LONG_SL_LINE)
            for level in ("long1", "long2", "long3"):
                pos_key = f"{ctx.symbol}:{level}"
                if ctx.portfolio.has_position(pos_key):
                    pos = ctx.portfolio.get_position(pos_key)
                    orders.append(Order(
                        symbol=pos_key, side=OrderSide.SELL,
                        order_type=OrderType.MARKET, quantity=pos.quantity,
                        price=sl_price,
                        leverage=self.leverage, reduce_only=True,
                    ))
                    self._entry_bar.pop(pos_key, None)

        # ALL_SHORT_SL: close S1-S3
        if (ctx.signals.get("all_short_sl", 0) >= 1.0
                and self._bars_above_orange >= self.sl_bars_confirm):
            sl_price = self._touch_price(ctx, _SHORT_SL_LINE)
            for level in ("short1", "short2", "short3"):
                pos_key = f"{ctx.symbol}:{level}"
                if ctx.portfolio.has_position(pos_key):
                    pos = ctx.portfolio.get_position(pos_key)
                    orders.append(Order(
                        symbol=pos_key, side=OrderSide.BUY,
                        order_type=OrderType.MARKET, quantity=pos.quantity,
                        price=sl_price,
                        leverage=self.leverage, reduce_only=True,
                    ))
                    self._entry_bar.pop(pos_key, None)

        # Per-level long TP/close
        for level, tp_signal in _LONG_TP.items():
            if not self.enable_breakout_levels and level == _BREAKOUT_LONG_LEVEL:
                continue
            pos_key = f"{ctx.symbol}:{level}"
            if (ctx.signals.get(tp_signal, 0) >= 1.0
                    and ctx.portfolio.has_position(pos_key)):
                # min_hold_bars gate
                entry_bar = self._entry_bar.get(pos_key, 0)
                if self.min_hold_bars > 0 and (ctx.bar_idx - entry_bar) < self.min_hold_bars:
                    continue
                pos = ctx.portfolio.get_position(pos_key)
                tp_price = self._touch_price(ctx, _LONG_TP_LINE.get(level))
                tp_frac = self.tp_fracs_by_level.get(level, 1.0)
                close_qty = pos.quantity if tp_frac >= 1.0 else pos.quantity * max(tp_frac, 0.0)
                if close_qty <= 0:
                    continue
                orders.append(Order(
                    symbol=pos_key, side=OrderSide.SELL,
                    order_type=OrderType.MARKET, quantity=close_qty,
                    price=tp_price,
                    leverage=self.leverage, reduce_only=True,
                ))
                if tp_frac >= 1.0:
                    self._entry_bar.pop(pos_key, None)

        # Per-level short TP/close
        for level, tp_signal in _SHORT_TP.items():
            if not self.enable_breakout_levels and level == _BREAKOUT_SHORT_LEVEL:
                continue
            pos_key = f"{ctx.symbol}:{level}"
            if (ctx.signals.get(tp_signal, 0) >= 1.0
                    and ctx.portfolio.has_position(pos_key)):
                # min_hold_bars gate
                entry_bar = self._entry_bar.get(pos_key, 0)
                if self.min_hold_bars > 0 and (ctx.bar_idx - entry_bar) < self.min_hold_bars:
                    continue
                pos = ctx.portfolio.get_position(pos_key)
                tp_price = self._touch_price(ctx, _SHORT_TP_LINE.get(level))
                tp_frac = self.tp_fracs_by_level.get(level, 1.0)
                close_qty = pos.quantity if tp_frac >= 1.0 else pos.quantity * max(tp_frac, 0.0)
                if close_qty <= 0:
                    continue
                orders.append(Order(
                    symbol=pos_key, side=OrderSide.BUY,
                    order_type=OrderType.MARKET, quantity=close_qty,
                    price=tp_price,
                    leverage=self.leverage, reduce_only=True,
                ))
                if tp_frac >= 1.0:
                    self._entry_bar.pop(pos_key, None)

        # ── 2. Entry signals ─────────────────────────────────────────

        long_levels = _PULLBACK_LONG_LEVELS + ((_BREAKOUT_LONG_LEVEL,) if self.enable_breakout_levels else ())
        short_levels = list(_PULLBACK_SHORT_LEVELS)
        if not self.enable_short3_level:
            short_levels.remove("short3")
        if self.enable_breakout_levels:
            short_levels.append(_BREAKOUT_SHORT_LEVEL)
        long_entries_allowed_now = self._entry_allowed_now(ctx, side="long")
        short_entries_allowed_now = self._entry_allowed_now(ctx, side="short")

        for level in long_levels:
            pos_key = f"{ctx.symbol}:{level}"
            if (ctx.signals.get(level, 0) >= 1.0
                    and self._level_enabled(level, self.enabled_long_levels)
                    and long_entries_allowed_now
                    and not ctx.portfolio.has_position(pos_key)):
                qty = self._position_size_for_side(
                    ctx.portfolio.total_equity, atr * self.atr_sl_mult,
                    ctx.bar.close, side="long",
                    weight=self.leg_weights_by_level.get(level, 1.0),
                )
                if qty > 0:
                    entry_price = self._touch_price(ctx, _LONG_ENTRY_LINE.get(level))
                    orders.append(Order(
                        symbol=pos_key, side=OrderSide.BUY,
                        order_type=OrderType.MARKET, quantity=qty,
                        price=entry_price,
                        leverage=self.leverage,
                    ))
                    self._entry_bar[pos_key] = ctx.bar_idx

        for level in short_levels:
            pos_key = f"{ctx.symbol}:{level}"
            if (ctx.signals.get(level, 0) >= 1.0
                    and self._level_enabled(level, self.enabled_short_levels)
                    and short_entries_allowed_now
                    and not ctx.portfolio.has_position(pos_key)):
                qty = self._position_size_for_side(
                    ctx.portfolio.total_equity, atr * self.atr_sl_mult,
                    ctx.bar.close, side="short",
                    weight=self.leg_weights_by_level.get(level, 1.0),
                )
                if qty > 0:
                    entry_price = self._touch_price(ctx, _SHORT_ENTRY_LINE.get(level))
                    orders.append(Order(
                        symbol=pos_key, side=OrderSide.SELL,
                        order_type=OrderType.MARKET, quantity=qty,
                        price=entry_price,
                        leverage=self.leverage,
                    ))
                    self._entry_bar[pos_key] = ctx.bar_idx

        return orders

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _touch_price(ctx: BarContext, line_name: str | None) -> float | None:
        """Return the channel line value if available, else None."""
        if line_name is None:
            return None
        val = ctx.signals.get(line_name)
        if val is None or math.isnan(val) or val <= 0:
            return None
        return val

    def _position_size(self, equity: float, stop_distance: float, *, weight: float = 1.0) -> float:
        """Fixed-fraction risk with optional per-leg weight."""
        risk_amount = equity * self.risk_pct * weight
        return round(risk_amount / stop_distance, 4)

    def _position_size_for_side(
        self, equity: float, stop_distance: float, price: float, *,
        side: str, weight: float = 1.0,
    ) -> float:
        """Choose sizing mode: flat fraction (pos_frac) or ATR-based risk."""
        frac = self.pos_frac_long if side == "long" else self.pos_frac_short
        if frac > 0 and price > 0:
            return round(equity * frac / price, 4)
        return self._position_size(equity, stop_distance, weight=weight)

    @staticmethod
    def _normalize_hours(hours: Iterable[int] | None) -> set[int]:
        """Normalize an iterable of UTC hours into a validated set."""
        return {
            int(hour) for hour in (hours or [])
            if 0 <= int(hour) <= 23
        }

    @staticmethod
    def _normalize_levels(levels: Iterable[str] | None, allowed: tuple[str, ...]) -> set[str]:
        """Normalize strategy level filters without forcing a default allow-list."""
        return {str(level) for level in (levels or []) if str(level) in allowed}

    @staticmethod
    def _level_enabled(level: str, enabled_levels: set[str]) -> bool:
        """Treat an empty filter set as 'all enabled' for backward compatibility."""
        return not enabled_levels or level in enabled_levels

    def _entry_allowed_now(self, ctx: BarContext, *, side: str) -> bool:
        """Return True when the current UTC hour is allowed for a side."""
        timestamp = getattr(ctx.bar, "timestamp", None)
        if timestamp is None or getattr(timestamp, "hour", None) is None:
            return True
        if side == "long":
            allowed_hours = self.allowed_long_entry_hours_utc or self.allowed_entry_hours_utc
        elif side == "short":
            allowed_hours = self.allowed_short_entry_hours_utc or self.allowed_entry_hours_utc
        else:
            allowed_hours = self.allowed_entry_hours_utc
        if not allowed_hours:
            return True
        return int(timestamp.hour) in allowed_hours
