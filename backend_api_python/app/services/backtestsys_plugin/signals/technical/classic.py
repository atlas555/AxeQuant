"""Classic technical indicators: SMA, EMA, ATR, RSI."""

from __future__ import annotations

import pandas as pd

from app.services.backtestsys_plugin.signals.base import Signal, SignalFrame, SignalType
from app.services.backtestsys_plugin.signals.registry import SignalRegistry


@SignalRegistry.register("sma")
class SmaSignal(Signal):
    """Simple Moving Average of close price."""

    def __init__(self, period: int = 20):
        self._period = period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        values = data["close"].rolling(window=self._period).mean()
        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=values,
        )

    @property
    def lookback(self) -> int:
        return self._period

    @property
    def params(self) -> dict:
        return {"period": self._period}


@SignalRegistry.register("ema")
class EmaSignal(Signal):
    """Exponential Moving Average of close price."""

    def __init__(self, period: int = 20):
        self._period = period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        values = data["close"].ewm(span=self._period, adjust=False).mean()
        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=values,
        )

    @property
    def lookback(self) -> int:
        return self._period * 3

    @property
    def params(self) -> dict:
        return {"period": self._period}


@SignalRegistry.register("atr")
class AtrSignal(Signal):
    """Average True Range using Wilder's smoothing."""

    def __init__(self, period: int = 14):
        self._period = period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        high = data["high"]
        low = data["low"]
        prev_close = data["close"].shift(1)

        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        # Wilder's smoothing: ewm with alpha = 1/period
        atr = tr.ewm(alpha=1.0 / self._period, adjust=False).mean()
        # First `period` values are unreliable
        atr.iloc[: self._period] = float("nan")

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=atr,
        )

    @property
    def lookback(self) -> int:
        return self._period + 1

    @property
    def params(self) -> dict:
        return {"period": self._period}


@SignalRegistry.register("rsi")
class RsiSignal(Signal):
    """Relative Strength Index using Wilder's smoothing."""

    def __init__(self, period: int = 14):
        self._period = period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        delta = data["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        # Wilder's smoothing
        avg_gain = gain.ewm(alpha=1.0 / self._period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / self._period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # First `period` values are unreliable
        rsi.iloc[: self._period] = float("nan")

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=rsi,
        )

    @property
    def lookback(self) -> int:
        return self._period + 1

    @property
    def params(self) -> dict:
        return {"period": self._period}
