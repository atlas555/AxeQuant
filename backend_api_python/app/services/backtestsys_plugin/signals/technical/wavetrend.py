"""WaveTrend oscillator signal."""

from __future__ import annotations

import pandas as pd

from app.services.backtestsys_plugin.signals.base import Signal, SignalFrame, SignalType
from app.services.backtestsys_plugin.signals.registry import SignalRegistry


@SignalRegistry.register("wavetrend")
class WaveTrendSignal(Signal):
    """WaveTrend oscillator with cross detection at OB/OS levels.

    Computes wt1 (smoothed CI), wt2 (MA of wt1), and cross signals
    filtered by overbought/oversold thresholds.
    """

    def __init__(
        self,
        n1: int = 10,
        n2: int = 21,
        smoothing: int = 4,
        ma_period: int = 4,
        ob_level: int = 53,
        os_level: int = -53,
    ):
        self._n1 = n1
        self._n2 = n2
        self._smoothing = smoothing
        self._ma_period = ma_period
        self._ob_level = ob_level
        self._os_level = os_level

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        ap = (data["high"] + data["low"] + data["close"]) / 3
        esa = ap.ewm(span=self._n1, adjust=False).mean()
        d = (ap - esa).abs().ewm(span=self._n1, adjust=False).mean()
        ci = (ap - esa) / (0.015 * d)

        # Optional extra smoothing
        if self._smoothing > 0:
            ci = ci.ewm(span=self._smoothing, adjust=False).mean()

        wt1 = ci.ewm(span=self._n2, adjust=False).mean()
        wt2 = wt1.rolling(window=self._ma_period).mean()

        histogram = wt1 - wt2

        # Cross detection filtered by OB/OS levels
        wt1_prev = wt1.shift(1)
        wt2_prev = wt2.shift(1)

        cross_up = (wt1_prev < wt2_prev) & (wt1 >= wt2) & (wt1_prev < self._os_level)
        cross_down = (wt1_prev > wt2_prev) & (wt1 <= wt2) & (wt1_prev > self._ob_level)

        # Cross subtypes for regime-adaptive interpretation
        raw_cross_up = (wt1_prev < wt2_prev) & (wt1 >= wt2)
        raw_cross_down = (wt1_prev > wt2_prev) & (wt1 <= wt2)

        # Zero-line crosses (between OS and 0 — breakout candidates)
        cross_up_zero = raw_cross_up & (wt1_prev < 0) & (wt1_prev >= self._os_level)
        cross_down_zero = raw_cross_down & (wt1_prev > 0) & (wt1_prev <= self._ob_level)

        # Pullback crosses (shallow, within trend)
        pullback_bull_cross = raw_cross_up & (wt1_prev > self._os_level) & (wt1_prev < 0)
        pullback_bear_cross = raw_cross_down & (wt1_prev < self._ob_level) & (wt1_prev > 0)

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=wt1,
            metadata={
                "wt2": wt2,
                "histogram": histogram,
                "cross_up": cross_up,
                "cross_down": cross_down,
                "cross_up_zero": cross_up_zero,
                "cross_down_zero": cross_down_zero,
                "pullback_bull_cross": pullback_bull_cross,
                "pullback_bear_cross": pullback_bear_cross,
            },
        )

    @property
    def lookback(self) -> int:
        return max(self._n1, self._n2) * 3

    @property
    def params(self) -> dict:
        return {
            "n1": self._n1,
            "n2": self._n2,
            "smoothing": self._smoothing,
            "ma_period": self._ma_period,
            "ob_level": self._ob_level,
            "os_level": self._os_level,
        }
