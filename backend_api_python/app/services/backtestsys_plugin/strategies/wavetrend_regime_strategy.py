"""WaveTrend Regime-Adaptive strategy — Architecture D implementation.

Reads the market regime from ``regime_detector`` signal and adapts
WaveTrend signal interpretation per regime:

- STRONG_TREND (1):  pullback entries only in trend direction
- EXHAUSTION (2):    OB/OS reversal signals valid
- ACCUMULATION (3):  zero-line crosses = breakout candidates
- DISTRIBUTION (4):  only OB/OS + CVD divergence confirmation
- LIQUIDATION (5):   all signals suspended
"""

from __future__ import annotations

from app.services.backtestsys_plugin.core.types import Order, OrderSide, OrderType
from app.services.backtestsys_plugin.strategies.base import BarContext, Strategy
from app.services.backtestsys_plugin.strategies.registry import StrategyRegistry

# Regime constants
REGIME_STRONG_TREND = 1
REGIME_EXHAUSTION = 2
REGIME_ACCUMULATION = 3
REGIME_DISTRIBUTION = 4
REGIME_LIQUIDATION = 5


@StrategyRegistry.register("wavetrend_regime")
class WaveTrendRegimeStrategy(Strategy):
    """Regime-adaptive WaveTrend strategy."""

    @classmethod
    def from_config(cls, cfg) -> "WaveTrendRegimeStrategy":
        return cls(
            pos_frac=getattr(cfg, "pos_frac", 0.5),
            min_hold_bars=getattr(cfg, "min_hold_bars", 0),
            leverage=getattr(cfg, "leverage", 3),
            enable_trend=getattr(cfg, "enable_trend_regime", True),
            enable_exhaust=getattr(cfg, "enable_exhaust_regime", True),
            enable_accum=getattr(cfg, "enable_accum_regime", True),
            enable_distrib=getattr(cfg, "enable_distrib_regime", True),
        )

    def __init__(
        self,
        pos_frac: float = 0.5,
        min_hold_bars: int = 0,
        leverage: int = 3,
        enable_trend: bool = True,
        enable_exhaust: bool = True,
        enable_accum: bool = True,
        enable_distrib: bool = True,
    ) -> None:
        self.pos_frac = pos_frac
        self.min_hold_bars = min_hold_bars
        self.leverage = leverage
        self.enable_trend = enable_trend
        self.enable_exhaust = enable_exhaust
        self.enable_accum = enable_accum
        self.enable_distrib = enable_distrib
        self._entry_bar: dict[str, int] = {}

    def on_bar(self, ctx: BarContext) -> list[Order]:
        orders: list[Order] = []

        # Read regime
        regime = int(ctx.signals.get("regime_detector", REGIME_DISTRIBUTION))

        # Read WT cross subtypes
        cross_up = ctx.signals.get("cross_up", 0)
        cross_down = ctx.signals.get("cross_down", 0)
        cross_up_zero = ctx.signals.get("cross_up_zero", 0)
        cross_down_zero = ctx.signals.get("cross_down_zero", 0)
        pullback_bull = ctx.signals.get("pullback_bull_cross", 0)
        pullback_bear = ctx.signals.get("pullback_bear_cross", 0)

        # Read regime metadata
        bull_trend = ctx.signals.get("bull_trend", 0)
        bullish_div = ctx.signals.get("bullish_div", 0)
        bearish_div = ctx.signals.get("bearish_div", 0)

        # Determine adaptive signal based on regime
        go_long = False
        go_short = False

        if regime == REGIME_LIQUIDATION:
            pass  # All signals suspended

        elif regime == REGIME_STRONG_TREND and self.enable_trend:
            if bull_trend and pullback_bull:
                go_long = True
            if not bull_trend and pullback_bear:
                go_short = True

        elif regime == REGIME_EXHAUSTION and self.enable_exhaust:
            if cross_up:
                go_long = True
            if cross_down:
                go_short = True

        elif regime == REGIME_ACCUMULATION and self.enable_accum:
            if cross_up or cross_up_zero:
                go_long = True
            if cross_down or cross_down_zero:
                go_short = True

        elif regime == REGIME_DISTRIBUTION and self.enable_distrib:
            if cross_up and bullish_div:
                go_long = True
            if cross_down and bearish_div:
                go_short = True

        # Any WT cross can trigger exit (regime-independent)
        any_cross_up = cross_up or cross_up_zero or pullback_bull
        any_cross_down = cross_down or cross_down_zero or pullback_bear

        long_key = f"{ctx.symbol}:wt_long"
        short_key = f"{ctx.symbol}:wt_short"
        has_long = ctx.portfolio.has_position(long_key)
        has_short = ctx.portfolio.has_position(short_key)

        # ── Exit logic ───────────────────────────────────────────
        if has_long and any_cross_down:
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

        if has_short and any_cross_up:
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

        # ── Entry logic ──────────────────────────────────────────
        if not has_long and go_long:
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

        if not has_short and go_short:
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

    def _hold_satisfied(self, pos_key: str, bar_idx: int) -> bool:
        if self.min_hold_bars <= 0:
            return True
        entry = self._entry_bar.get(pos_key, 0)
        return (bar_idx - entry) >= self.min_hold_bars

    def _calc_qty(self, ctx: BarContext) -> float:
        price = ctx.bar.close
        if price <= 0:
            return 0.0
        return round(
            ctx.portfolio.total_equity * self.pos_frac / self.leverage / price,
            4,
        )
