"""WaveTrend + Order Flow strategy — toggleable OF gates for A/B testing.

Extends WaveTrendStrategy with independently toggleable order flow gates.
Each gate reads a corresponding signal from ctx.signals and vetoes the
WT entry if its condition is not met.  This allows one-variable-at-a-time
isolation: each YAML config enables exactly one gate.
"""

from __future__ import annotations

from app.services.backtestsys_plugin.core.types import Order, OrderSide, OrderType
from app.services.backtestsys_plugin.strategies.base import BarContext, Strategy
from app.services.backtestsys_plugin.strategies.registry import StrategyRegistry


@StrategyRegistry.register("wavetrend_of")
class WaveTrendOFStrategy(Strategy):
    """WaveTrend strategy with independently toggleable order flow gates."""

    # Gate → signal name mapping.
    _GATE_SIGNAL_MAP = {
        "gate_cvd_divergence": "cvd_divergence",
        "gate_delta_confirm": "true_delta",
        "gate_mfi_confluence": "mfi",
        "gate_vwap_side": "vwap_distance",
        "gate_absorption": "absorption",
        "gate_volume_threshold": "volume_threshold",
        "gate_volume_regime_adaptive": "volume_regime",
    }

    @classmethod
    def from_config(cls, cfg) -> "WaveTrendOFStrategy":
        gates = {}
        for gate_name in cls._GATE_SIGNAL_MAP:
            gates[gate_name] = getattr(cfg, gate_name, False)

        return cls(
            pos_frac=getattr(cfg, "pos_frac", 0.25),
            min_hold_bars=getattr(cfg, "min_hold_bars", 0),
            leverage=getattr(cfg, "leverage", 1),
            risk_per_trade=getattr(cfg, "risk_per_trade", 0.01),
            ob_level=getattr(cfg, "ob_level", 53),
            os_level=getattr(cfg, "os_level", -53),
            mfi_os=getattr(cfg, "mfi_os", 20.0),
            mfi_ob=getattr(cfg, "mfi_ob", 80.0),
            gates=gates,
        )

    def __init__(
        self,
        pos_frac: float = 0.25,
        min_hold_bars: int = 0,
        leverage: int = 1,
        risk_per_trade: float = 0.01,
        ob_level: float = 53.0,
        os_level: float = -53.0,
        mfi_os: float = 20.0,
        mfi_ob: float = 80.0,
        gates: dict[str, bool] | None = None,
    ) -> None:
        self.pos_frac = pos_frac
        self.min_hold_bars = min_hold_bars
        self.leverage = leverage
        self.risk_per_trade = risk_per_trade
        self.ob_level = ob_level
        self.os_level = os_level
        self.mfi_os = mfi_os
        self.mfi_ob = mfi_ob
        self.gates = gates or {}
        self._entry_bar: dict[str, int] = {}

    # ── Gate evaluation ──────────────────────────────────────────────

    def _check_gates(self, ctx: BarContext, direction: str) -> bool:
        """Return True if ALL enabled gates pass for the given direction.

        direction: 'long' or 'short'.
        """
        for gate_name, signal_name in self._GATE_SIGNAL_MAP.items():
            if not self.gates.get(gate_name, False):
                continue  # gate not enabled

            val = ctx.signals.get(signal_name, 0.0)

            if gate_name == "gate_cvd_divergence":
                # Veto if CVD diverges against trade direction.
                # Bullish div (+1) supports long; bearish div (-1) supports short.
                if direction == "long" and val < 0:
                    return False
                if direction == "short" and val > 0:
                    return False

            elif gate_name == "gate_delta_confirm":
                # Delta sign must match direction.
                if direction == "long" and val <= 0:
                    return False
                if direction == "short" and val >= 0:
                    return False

            elif gate_name == "gate_mfi_confluence":
                # MFI must be in same OB/OS zone as WT.
                if direction == "long" and val > self.mfi_os:
                    return False  # MFI not oversold
                if direction == "short" and val < self.mfi_ob:
                    return False  # MFI not overbought

            elif gate_name == "gate_vwap_side":
                # Price must be on the correct side of VWAP.
                if direction == "long" and val > 0:
                    return False  # price above VWAP → not ideal for long reversal
                if direction == "short" and val < 0:
                    return False  # price below VWAP → not ideal for short reversal

            elif gate_name == "gate_absorption":
                # Absorption candle must be present at WT extreme.
                if val < 1.0:
                    return False

            elif gate_name == "gate_volume_threshold":
                # Volume must exceed threshold.
                if val < 1.0:
                    return False

            elif gate_name == "gate_volume_regime_adaptive":
                # Reject signals in LOW volume regime.
                if val < 0:  # -1 = LOW
                    return False

        return True

    # ── Main entry point ─────────────────────────────────────────────

    def on_bar(self, ctx: BarContext) -> list[Order]:
        orders: list[Order] = []

        cross_up = ctx.signals.get("cross_up", 0)
        cross_down = ctx.signals.get("cross_down", 0)

        long_key = f"{ctx.symbol}:wt_long"
        short_key = f"{ctx.symbol}:wt_short"

        has_long = ctx.portfolio.has_position(long_key)
        has_short = ctx.portfolio.has_position(short_key)

        # ── 1. Exit logic (same as naked WT — no gates on exits) ─────

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

        # ── 2. Entry logic with OF gates ─────────────────────────────

        if not has_long and cross_up:
            if self._check_gates(ctx, "long"):
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
            if self._check_gates(ctx, "short"):
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

    # ── Helpers ───────────────────────────────────────────────────────

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
