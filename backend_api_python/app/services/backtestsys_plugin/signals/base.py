"""Signal base classes and data types for the backtesting engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd


class SignalType(Enum):
    """Type of signal output."""

    BINARY = "binary"
    CONTINUOUS = "continuous"
    CATEGORICAL = "categorical"
    GRADE = "grade"


@dataclass
class SignalFrame:
    """Container for a computed signal's output."""

    name: str
    signal_type: SignalType
    values: pd.Series
    confidence: Optional[pd.Series] = None
    metadata: dict = field(default_factory=dict)


class Signal(ABC):
    """Abstract base class for all signals."""

    @property
    def name(self) -> str:
        """Return the class name as the signal name."""
        return self.__class__.__name__

    @abstractmethod
    def compute(self, data: pd.DataFrame) -> SignalFrame:
        """Compute the signal from OHLCV data."""
        ...

    @property
    @abstractmethod
    def lookback(self) -> int:
        """Minimum number of bars needed before signal is valid."""
        ...

    @property
    @abstractmethod
    def params(self) -> dict:
        """Return the signal's parameters as a dict."""
        ...
