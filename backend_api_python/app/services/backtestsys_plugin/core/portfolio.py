"""Portfolio management for the AxeBacktest perpetual futures backtesting engine.

Tracks cash, open positions, equity curve, and completed trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.services.backtestsys_plugin.core.types import Bar, Fill, Order, OrderSide, Position, PositionSide, Trade


# ── PortfolioSnapshot (read-only view) ──────────────────────────────


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Immutable point-in-time view of the portfolio."""

    cash: float
    positions: Dict[str, Position]
    total_equity: float

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def get_position(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def has_any_position_for(self, symbol: str) -> bool:
        """Check if any position exists for a symbol (including compound keys like 'SYM:long1')."""
        return any(k == symbol or k.startswith(f"{symbol}:") for k in self.positions)

    def get_positions_for(self, symbol: str) -> Dict[str, Position]:
        """Get all positions for a symbol (including compound keys)."""
        return {k: v for k, v in self.positions.items()
                if k == symbol or k.startswith(f"{symbol}:")}


# ── Internal bookkeeping for open positions ─────────────────────────


@dataclass
class _OpenPositionMeta:
    """Tracks extra info needed when closing a position (entry fill data)."""

    position: Position
    entry_fill: Fill  # to retrieve entry bar_idx and entry fee
    entry_fee_basis: float


# ── Portfolio ───────────────────────────────────────────────────────


class Portfolio:
    """Manages cash, positions, equity curve, and trade log.

    Parameters
    ----------
    initial_capital : float
        Starting cash balance in quote currency (e.g. USDT).
    """

    def __init__(self, initial_capital: float) -> None:
        self.cash: float = initial_capital
        self._open: Dict[str, _OpenPositionMeta] = {}
        self.equity_curve: List[float] = []
        self.trade_log: List[Trade] = []

    # ── Properties ──────────────────────────────────────────────────

    @property
    def positions(self) -> Dict[str, Position]:
        """Current open positions keyed by symbol."""
        return {sym: meta.position for sym, meta in self._open.items()}

    @property
    def total_equity(self) -> float:
        """Cash + sum(margin + unrealized_pnl) across all positions."""
        pos_value = sum(
            meta.position.margin + meta.position.unrealized_pnl
            for meta in self._open.values()
        )
        return self.cash + pos_value

    # ── Fill processing ─────────────────────────────────────────────

    def apply_fill(self, fill: Fill) -> None:
        """Process a single fill, updating cash, positions, and trade log."""
        if fill.is_funding:
            self._apply_funding(fill)
            return

        if fill.order.reduce_only or fill.is_liquidation:
            self._close_position(fill)
        else:
            self._open_position(fill)

    def apply_fills_batch(self, fills: List[Fill]) -> None:
        """Process multiple fills in order."""
        for fill in fills:
            self.apply_fill(fill)

    # ── Multi-leg helpers ────────────────────────────────────────────

    # Known level suffixes for compound position keys (e.g. "BTCUSDT:long1")
    _LEVEL_SUFFIXES = frozenset([
        "long1", "long2", "long3", "long4",
        "short1", "short2", "short3", "short4",
    ])

    def has_any_position_for(self, symbol: str) -> bool:
        """Check if any position exists for a symbol (including compound keys)."""
        return any(k == symbol or k.startswith(f"{symbol}:") for k in self._open)

    def get_positions_for(self, symbol: str) -> Dict[str, Position]:
        """Get all positions for a symbol (including compound keys)."""
        return {k: meta.position for k, meta in self._open.items()
                if k == symbol or k.startswith(f"{symbol}:")}

    # ── Mark to market ──────────────────────────────────────────────

    def mark_to_market(self, bars: Dict[str, Bar]) -> None:
        """Update unrealized PnL for each open position using current bar data.

        Supports compound position keys like ``"BTCUSDT:long1"`` by matching
        bar keys against position key prefixes.
        """
        for sym, bar in bars.items():
            for pos_key, meta in self._open.items():
                if pos_key == sym or pos_key.startswith(f"{sym}:"):
                    pos = meta.position
                    pos.unrealized_pnl = (
                        (bar.close - pos.entry_price) * pos.quantity * pos.direction_sign
                    )

    # ── Equity recording ────────────────────────────────────────────

    def record_equity(self) -> None:
        """Append current total_equity to the equity curve."""
        self.equity_curve.append(self.total_equity)

    # ── Snapshot ────────────────────────────────────────────────────

    def snapshot(self) -> PortfolioSnapshot:
        """Return a read-only view of the current portfolio state."""
        return PortfolioSnapshot(
            cash=self.cash,
            positions=self.positions,
            total_equity=self.total_equity,
        )

    # ── Private helpers ─────────────────────────────────────────────

    def _apply_funding(self, fill: Fill) -> None:
        """Adjust cash and position funding_pnl for a funding rate event."""
        symbol = fill.order.symbol
        self.cash += fill.funding_pnl
        if symbol in self._open:
            self._open[symbol].position.funding_pnl += fill.funding_pnl

    def _open_position(self, fill: Fill) -> None:
        """Create a new position from an opening fill."""
        order = fill.order
        margin = fill.fill_price * fill.fill_quantity / order.leverage

        side = PositionSide.LONG if order.side == OrderSide.BUY else PositionSide.SHORT

        position = Position(
            symbol=order.symbol,
            side=side,
            quantity=fill.fill_quantity,
            entry_price=fill.fill_price,
            leverage=order.leverage,
            margin=margin,
        )

        self._open[order.symbol] = _OpenPositionMeta(
            position=position,
            entry_fill=fill,
            entry_fee_basis=fill.fee,
        )
        self.cash -= margin + fill.fee

    def _close_position(self, fill: Fill) -> None:
        """Close an existing position, record a Trade, return capital to cash."""
        meta = self._open[fill.order.symbol]
        pos = meta.position

        # Full close remains the fast path and preserves existing behavior.
        if fill.fill_quantity >= pos.quantity:
            self._close_position_full(fill, meta)
            return

        self._close_position_partial(fill, meta)

    def _close_position_full(self, fill: Fill, meta: _OpenPositionMeta) -> None:
        """Fully close a position."""
        pos = meta.position
        entry_fill = meta.entry_fill

        # Gross PnL
        gross_pnl = (
            (fill.fill_price - pos.entry_price)
            * fill.fill_quantity
            * pos.direction_sign
        )

        # Return margin + profit - close fee + accumulated funding to cash
        self.cash += pos.margin + gross_pnl - fill.fee + pos.funding_pnl

        # Record completed trade
        trade = Trade(
            entry_bar=entry_fill.bar_idx,
            exit_bar=fill.bar_idx,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=fill.fill_price,
            quantity=fill.fill_quantity,
            leverage=pos.leverage,
            fee=meta.entry_fee_basis + fill.fee,
            is_liquidated=fill.is_liquidation,
            funding_pnl=pos.funding_pnl,
        )
        self.trade_log.append(trade)

        # Remove position
        del self._open[pos.symbol]

    def _close_position_partial(self, fill: Fill, meta: _OpenPositionMeta) -> None:
        """Partially close a position and keep the residual open."""
        pos = meta.position
        entry_fill = meta.entry_fill

        close_fraction = fill.fill_quantity / pos.quantity
        remaining_quantity = pos.quantity - fill.fill_quantity

        closed_margin = pos.margin * close_fraction
        closed_entry_fee = meta.entry_fee_basis * close_fraction
        closed_funding_pnl = pos.funding_pnl * close_fraction

        gross_pnl = (
            (fill.fill_price - pos.entry_price)
            * fill.fill_quantity
            * pos.direction_sign
        )

        self.cash += closed_margin + gross_pnl - fill.fee + closed_funding_pnl

        trade = Trade(
            entry_bar=entry_fill.bar_idx,
            exit_bar=fill.bar_idx,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=fill.fill_price,
            quantity=fill.fill_quantity,
            leverage=pos.leverage,
            fee=closed_entry_fee + fill.fee,
            is_liquidated=fill.is_liquidation,
            funding_pnl=closed_funding_pnl,
        )
        self.trade_log.append(trade)

        pos.quantity = remaining_quantity
        pos.margin -= closed_margin
        pos.unrealized_pnl *= remaining_quantity / (remaining_quantity + fill.fill_quantity)
        pos.funding_pnl -= closed_funding_pnl
        meta.entry_fee_basis -= closed_entry_fee
