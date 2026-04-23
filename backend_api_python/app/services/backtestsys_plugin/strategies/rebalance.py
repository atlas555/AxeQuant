"""Calendar/drift-triggered rebalance strategy for multi-asset portfolios.

Supports equal-weight and custom-weight allocations with monthly/quarterly
rebalancing and drift-triggered re-allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.backtestsys_plugin.core.types import Order, OrderSide, OrderType
from app.services.backtestsys_plugin.strategies.base import BarContext, Strategy
from app.services.backtestsys_plugin.strategies.registry import StrategyRegistry


@dataclass
class RebalanceConfig:
    assets: dict[str, float]            # symbol -> target weight (must sum to 1.0)
    frequency: str = "monthly"          # "monthly" | "quarterly"
    drift_threshold: float = 0.05       # 5% drift triggers rebalance
    leverage: int = 1
    min_trade_usd: float = 100.0        # minimum trade size


@StrategyRegistry.register("rebalance")
class RebalanceStrategy(Strategy):
    """Periodic rebalance with drift trigger.

    Lifecycle
    ---------
    1. **Initial allocation** — the very first ``on_bar`` call triggers an
       unconditional rebalance so the portfolio moves from cash to the target
       weights.
    2. **Calendar trigger** — on the first bar of a new rebalance period
       (monthly: every new month; quarterly: months 1, 4, 7, 10).
    3. **Drift trigger** — after the initial allocation, if any asset's actual
       weight deviates from target by more than ``drift_threshold``.
    """

    @classmethod
    def from_config(cls, cfg) -> "RebalanceStrategy":
        """Create from a StrategyConfig (with extra fields) or RebalanceConfig."""
        if isinstance(cfg, RebalanceConfig):
            return cls(cfg)
        assets = cfg.assets if hasattr(cfg, 'assets') else {}
        frequency = cfg.frequency if hasattr(cfg, 'frequency') else "monthly"
        drift = cfg.drift_threshold if hasattr(cfg, 'drift_threshold') else 0.05
        leverage = cfg.leverage if hasattr(cfg, 'leverage') else 1
        return cls(RebalanceConfig(
            assets=assets, frequency=frequency,
            drift_threshold=drift, leverage=leverage,
        ))

    def __init__(self, config: RebalanceConfig):
        self.config = config
        self._last_rebalance_month: int | None = None
        self._initialized = False

    def on_bar(self, ctx: BarContext) -> list[Order]:
        ts = ctx.bar.timestamp
        should_rebalance = False

        # Initial allocation
        if not self._initialized:
            should_rebalance = True

        # Calendar trigger
        elif self._is_rebalance_period(ts):
            should_rebalance = True

        # Drift trigger
        elif self._check_drift(ctx):
            should_rebalance = True

        if not should_rebalance:
            return []

        self._initialized = True
        if hasattr(ts, 'month'):
            self._last_rebalance_month = ts.month

        return self._generate_rebalance_orders(ctx)

    def _is_rebalance_period(self, ts) -> bool:
        month = ts.month if hasattr(ts, 'month') else 1
        if self._last_rebalance_month == month:
            return False
        if self.config.frequency == "monthly":
            return month != self._last_rebalance_month
        if self.config.frequency == "quarterly":
            return month in (1, 4, 7, 10) and month != self._last_rebalance_month
        return False

    def _check_drift(self, ctx: BarContext) -> bool:
        """Check if any asset has drifted beyond threshold.

        Only triggers when the portfolio already holds at least one position
        for a tracked asset — otherwise the deviation is due to the initial
        allocation not yet having occurred, which is handled separately.
        """
        equity = ctx.portfolio.total_equity
        if equity <= 0:
            return False

        # Guard: require at least one tracked position to exist
        has_any = any(
            ctx.portfolio.get_position(s) is not None
            for s in self.config.assets
        )
        if not has_any:
            return False

        for symbol, target_w in self.config.assets.items():
            pos = ctx.portfolio.get_position(symbol)
            if pos is None:
                actual_w = 0.0
            else:
                # Use current market price, not entry price
                current_value = ctx.bar.close * pos.quantity
                actual_w = current_value / equity
            if abs(actual_w - target_w) > self.config.drift_threshold:
                return True
        return False

    def _generate_rebalance_orders(self, ctx: BarContext) -> list[Order]:
        """Generate orders to reach target weights."""
        equity = ctx.portfolio.total_equity
        orders: list[Order] = []
        target_w = self.config.assets.get(ctx.symbol, 0)
        if target_w <= 0:
            return []

        target_value = equity * target_w
        pos = ctx.portfolio.get_position(ctx.symbol)
        # Use current market price for current position value
        current_value = (ctx.bar.close * pos.quantity) if pos else 0

        diff = target_value - current_value
        if abs(diff) < self.config.min_trade_usd:
            return []

        price = ctx.bar.close
        qty = round(abs(diff) / price, 6)
        side = OrderSide.BUY if diff > 0 else OrderSide.SELL

        orders.append(Order(
            symbol=ctx.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            leverage=self.config.leverage,
            reduce_only=(diff < 0 and pos is not None),
        ))

        return orders
