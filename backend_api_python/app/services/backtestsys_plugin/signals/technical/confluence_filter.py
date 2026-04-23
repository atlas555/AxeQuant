"""WaveTrend + Order Flow confluence filter — reusable module.

Computes a binary ``confluence_pass`` signal that other strategies can
import and use as an entry gate.  The filter combines three layers:

1. **WaveTrend extreme**: WT1 crosses WT2 at OB/OS zones.
2. **Volume Regime gate**: reject signals in LOW volume (< 0.7× SMA).
3. **Delta Confirm gate**: require per-bar taker delta direction match.

Usage in another strategy::

    from app.services.backtestsys_plugin.signals.technical.confluence_filter import ConfluenceFilterSignal

Or via YAML config::

    signals:
      confluence:
        type: confluence_filter
        params:
          n1: 25
          n2: 42
          smoothing: 4
          ob_level: 75
          os_level: -40
          vol_period: 20
          vol_low_threshold: 0.7

The output SignalFrame contains:
    - values: 1.0 (long pass), -1.0 (short pass), 0.0 (no signal)
    - metadata["wt1"], metadata["wt2"]: oscillator lines for plotting
    - metadata["cross_up"], metadata["cross_down"]: raw WT crosses
    - metadata["vol_regime"]: -1/0/1 volume classification
    - metadata["delta"]: per-bar volume delta
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtestsys_plugin.signals.base import Signal, SignalFrame, SignalType
from app.services.backtestsys_plugin.signals.registry import SignalRegistry


def _compute_delta(data: pd.DataFrame) -> pd.Series:
    """Per-bar volume delta (true taker or OHLCV proxy)."""
    if "taker_buy_volume" in data.columns:
        buy = data["taker_buy_volume"].astype(float)
        sell = data["volume"].astype(float) - buy
        return buy - sell
    sign = np.sign(data["close"] - data["open"]).replace(0, 1)
    return sign * data["volume"]


@SignalRegistry.register("confluence_filter")
class ConfluenceFilterSignal(Signal):
    """WaveTrend + VolRegime + DeltaConfirm confluence filter.

    Returns +1 (long confluence), -1 (short confluence), or 0 (no signal).
    """

    def __init__(
        self,
        # WaveTrend params
        n1: int = 25,
        n2: int = 42,
        smoothing: int = 4,
        ma_period: int = 4,
        ob_level: float = 75.0,
        os_level: float = -40.0,
        # Volume Regime params
        vol_period: int = 20,
        vol_low_threshold: float = 0.7,
        # Delta gate
        require_delta: bool = True,
        # Volume regime gate
        reject_low_volume: bool = True,
    ) -> None:
        self._n1 = n1
        self._n2 = n2
        self._smoothing = smoothing
        self._ma_period = ma_period
        self._ob = ob_level
        self._os = os_level
        self._vol_period = vol_period
        self._vol_low = vol_low_threshold
        self._require_delta = require_delta
        self._reject_low_vol = reject_low_volume

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        # --- WaveTrend ---
        ap = (data["high"] + data["low"] + data["close"]) / 3
        esa = ap.ewm(span=self._n1, adjust=False).mean()
        d = (ap - esa).abs().ewm(span=self._n1, adjust=False).mean()
        ci = (ap - esa) / (0.015 * d)
        if self._smoothing > 0:
            ci = ci.ewm(span=self._smoothing, adjust=False).mean()
        wt1 = ci.ewm(span=self._n2, adjust=False).mean()
        wt2 = wt1.rolling(window=self._ma_period).mean()

        wt1_prev = wt1.shift(1)
        wt2_prev = wt2.shift(1)
        cross_up = (wt1_prev < wt2_prev) & (wt1 >= wt2) & (wt1_prev < self._os)
        cross_down = (wt1_prev > wt2_prev) & (wt1 <= wt2) & (wt1_prev > self._ob)

        # --- Volume Regime ---
        vol_sma = data["volume"].rolling(self._vol_period).mean()
        vol_ratio = data["volume"] / vol_sma
        vol_regime = pd.Series(0.0, index=data.index)
        vol_regime[vol_ratio < self._vol_low] = -1.0  # LOW
        vol_regime[vol_ratio > 1.5] = 1.0  # HIGH

        # --- Delta ---
        delta = _compute_delta(data)

        # --- Combine gates ---
        result = pd.Series(0.0, index=data.index)

        for i in range(len(data)):
            if cross_up.iloc[i]:
                # Gate 1: Volume regime
                if self._reject_low_vol and vol_regime.iloc[i] < 0:
                    continue
                # Gate 2: Delta direction
                if self._require_delta and delta.iloc[i] <= 0:
                    continue
                result.iloc[i] = 1.0  # long pass

            elif cross_down.iloc[i]:
                if self._reject_low_vol and vol_regime.iloc[i] < 0:
                    continue
                if self._require_delta and delta.iloc[i] >= 0:
                    continue
                result.iloc[i] = -1.0  # short pass

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CATEGORICAL,
            values=result,
            metadata={
                "wt1": wt1,
                "wt2": wt2,
                "histogram": wt1 - wt2,
                "cross_up": cross_up.astype(float),
                "cross_down": cross_down.astype(float),
                "vol_regime": vol_regime,
                "delta": delta,
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
            "ob_level": self._ob,
            "os_level": self._os,
            "vol_period": self._vol_period,
            "vol_low_threshold": self._vol_low,
            "require_delta": self._require_delta,
            "reject_low_volume": self._reject_low_vol,
        }
