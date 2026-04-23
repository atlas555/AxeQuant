"""Base strategy abstractions for the AxeBacktest engine.

Defines :class:`StrategyConfig`, :class:`BarContext`, and the
:class:`Strategy` ABC that all concrete strategies must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from app.services.backtestsys_plugin.config.loader import StrategyConfig as StrategyConfig  # re-export for backward compat
from app.services.backtestsys_plugin.core.types import Fill, Order


# ── Protocol types for BarContext fields ────────────────────────────


@runtime_checkable
class BarLike(Protocol):
    close: float
    open: float
    high: float
    low: float
    volume: float
    timestamp: object


@runtime_checkable
class PortfolioLike(Protocol):
    cash: float
    total_equity: float
    def has_position(self, symbol: str) -> bool: ...
    def get_position(self, symbol: str) -> Optional[object]: ...
    def has_any_position_for(self, symbol: str) -> bool: ...


# ── Bar context passed to on_bar ────────────────────────────────────

@dataclass
class BarContext:
    """Everything a strategy needs to make decisions on a single bar."""

    bar_idx: int
    bar: BarLike                     # Bar or duck-typed object with OHLCV fields
    signals: dict[str, float]        # signal_name -> value
    portfolio: PortfolioLike         # PortfolioSnapshot (has .has_position(), .total_equity)
    symbol: str


# ── Strategy ABC ────────────────────────────────────────────────────

class Strategy(ABC):
    """Abstract base class for all backtesting strategies."""

    @abstractmethod
    def on_bar(self, ctx: BarContext) -> list[Order]:
        """Evaluate the current bar and return zero or more orders."""
        ...

    def on_fill(self, fill: Fill) -> None:
        """Optional hook called when an order is filled.

        The default implementation is a no-op.
        """
