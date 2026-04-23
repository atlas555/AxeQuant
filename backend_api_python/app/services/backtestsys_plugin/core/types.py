"""Core data types for the AxeBacktest perpetual futures backtesting engine.

All other modules depend on these foundational types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd


# ── Enums ────────────────────────────────────────────────────────────

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop"
    TAKE_PROFIT = "tp"


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


# ── Order ────────────────────────────────────────────────────────────

@dataclass
class Order:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    leverage: int = 1
    reduce_only: bool = False
    submitted_at: int = 0
    execute_at: int = 0


# ── Fill ─────────────────────────────────────────────────────────────

@dataclass
class Fill:
    order: Order
    fill_price: float
    fill_quantity: float
    fee: float
    bar_idx: int
    is_liquidation: bool = False
    is_funding: bool = False
    funding_pnl: float = 0.0


# ── Position ─────────────────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    side: PositionSide  # PositionSide.LONG | PositionSide.SHORT
    quantity: float
    entry_price: float
    leverage: int
    margin: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    funding_pnl: float = 0.0

    @property
    def direction_sign(self) -> int:
        """1 for long, -1 for short."""
        return 1 if self.side == PositionSide.LONG else -1

    @property
    def notional_value(self) -> float:
        """entry_price * quantity."""
        return self.entry_price * self.quantity


# ── Bar ──────────────────────────────────────────────────────────────

@dataclass
class Bar:
    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_series(cls, s: pd.Series) -> Bar:
        """Create a Bar from a pandas Series with OHLCV columns.

        Uses ``s.name`` as the timestamp.
        """
        return cls(
            timestamp=s.name,
            open=float(s["open"]),
            high=float(s["high"]),
            low=float(s["low"]),
            close=float(s["close"]),
            volume=float(s["volume"]),
        )


# ── Trade ────────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    side: PositionSide  # PositionSide.LONG | PositionSide.SHORT
    entry_price: float
    exit_price: float
    quantity: float
    leverage: int
    fee: float
    is_liquidated: bool
    funding_pnl: float = 0.0
    mae: float = 0.0  # Maximum Adverse Excursion
    mfe: float = 0.0  # Maximum Favorable Excursion

    @property
    def gross_pnl(self) -> float:
        """Gross PnL before fees and funding."""
        if self.side == PositionSide.LONG:
            return (self.exit_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.exit_price) * self.quantity

    @property
    def net_pnl(self) -> float:
        """Net PnL after fees and funding."""
        return self.gross_pnl - self.fee + self.funding_pnl

    @property
    def hold_bars(self) -> int:
        """Number of bars the trade was held."""
        return self.exit_bar - self.entry_bar
