"""Order flow signal modules for the AxeBacktest engine.

Provides volume-based signals that can gate or enhance WaveTrend entries:
- TrueDelta: per-bar buy/sell delta (from taker_buy_volume or OHLCV proxy)
- CVD: cumulative volume delta
- CvdDivergence: price vs CVD divergence detection
- MFI: Money Flow Index
- VwapDistance: normalized distance from session VWAP
- Absorption: high-volume tiny-body candle detection
- VolumeRegime: LOW / NORMAL / HIGH volume classification
- VolumeThreshold: binary volume gate
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtestsys_plugin.signals.base import Signal, SignalFrame, SignalType
from app.services.backtestsys_plugin.signals.registry import SignalRegistry


# ── Helpers ───────────────────────────────────────────────────────────


def _compute_delta(data: pd.DataFrame) -> pd.Series:
    """Compute per-bar volume delta.

    Uses ``taker_buy_volume`` when available (true delta).
    Falls back to ``sign(close - open) * volume`` (proxy).
    """
    if "taker_buy_volume" in data.columns:
        buy = data["taker_buy_volume"].astype(float)
        sell = data["volume"].astype(float) - buy
        return buy - sell
    # Proxy: positive candle → assume more buy volume.
    sign = np.sign(data["close"] - data["open"])
    sign = sign.replace(0, 1)  # doji → slight buy bias
    return sign * data["volume"]


def _rolling_vwap(data: pd.DataFrame, window: int) -> pd.Series:
    """Rolling VWAP over *window* bars."""
    tp = (data["high"] + data["low"] + data["close"]) / 3
    tp_vol = tp * data["volume"]
    return tp_vol.rolling(window).sum() / data["volume"].rolling(window).sum()


# ── TrueDelta ─────────────────────────────────────────────────────────


@SignalRegistry.register("true_delta")
class TrueDeltaSignal(Signal):
    """Per-bar volume delta (buy − sell)."""

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        delta = _compute_delta(data)
        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=delta,
        )

    @property
    def lookback(self) -> int:
        return 1

    @property
    def params(self) -> dict:
        return {}


# ── CVD ───────────────────────────────────────────────────────────────


@SignalRegistry.register("cvd")
class CvdSignal(Signal):
    """Cumulative Volume Delta with EMA slope indicator."""

    def __init__(self, ema_period: int = 20):
        self._ema_period = ema_period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        delta = _compute_delta(data)
        cvd = delta.cumsum()
        cvd_ema = cvd.ewm(span=self._ema_period, adjust=False).mean()
        # Slope: 1 = rising, -1 = falling
        cvd_slope = np.sign(cvd_ema - cvd_ema.shift(1))
        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=cvd,
            metadata={"cvd_ema": cvd_ema, "cvd_slope": cvd_slope},
        )

    @property
    def lookback(self) -> int:
        return self._ema_period * 3

    @property
    def params(self) -> dict:
        return {"ema_period": self._ema_period}


# ── CVD Divergence ────────────────────────────────────────────────────


@SignalRegistry.register("cvd_divergence")
class CvdDivergenceSignal(Signal):
    """Detect divergence between price and CVD.

    Returns -1 (bearish divergence), 0 (none), 1 (bullish divergence).

    Bearish: price makes higher high, CVD makes lower high.
    Bullish: price makes lower low, CVD makes higher low.
    """

    def __init__(self, lookback: int = 14):
        self._lookback = lookback

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        delta = _compute_delta(data)
        cvd = delta.cumsum()
        close = data["close"]

        lb = self._lookback
        price_hh = close.rolling(lb).max()
        price_ll = close.rolling(lb).min()
        cvd_hh = cvd.rolling(lb).max()
        cvd_ll = cvd.rolling(lb).min()

        # Compare current peak/trough to previous window.
        prev_price_hh = close.shift(lb).rolling(lb).max()
        prev_price_ll = close.shift(lb).rolling(lb).min()
        prev_cvd_hh = cvd.shift(lb).rolling(lb).max()
        prev_cvd_ll = cvd.shift(lb).rolling(lb).min()

        # Bearish divergence: price higher high, CVD lower high.
        bearish = (price_hh > prev_price_hh) & (cvd_hh < prev_cvd_hh)
        # Bullish divergence: price lower low, CVD higher low.
        bullish = (price_ll < prev_price_ll) & (cvd_ll > prev_cvd_ll)

        result = pd.Series(0.0, index=data.index)
        result[bearish] = -1.0
        result[bullish] = 1.0

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CATEGORICAL,
            values=result,
        )

    @property
    def lookback(self) -> int:
        return self._lookback * 3

    @property
    def params(self) -> dict:
        return {"lookback": self._lookback}


# ── MFI (Money Flow Index) ───────────────────────────────────────────


@SignalRegistry.register("mfi")
class MfiSignal(Signal):
    """Money Flow Index — volume-weighted RSI."""

    def __init__(self, period: int = 14):
        self._period = period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        tp = (data["high"] + data["low"] + data["close"]) / 3
        mf = tp * data["volume"]

        tp_delta = tp.diff()
        pos_flow = mf.where(tp_delta > 0, 0.0)
        neg_flow = mf.where(tp_delta < 0, 0.0)

        pos_sum = pos_flow.rolling(self._period).sum()
        neg_sum = neg_flow.rolling(self._period).sum()

        mfr = pos_sum / neg_sum.replace(0, np.nan)
        mfi = 100.0 - (100.0 / (1.0 + mfr))
        mfi.iloc[: self._period] = np.nan

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=mfi,
        )

    @property
    def lookback(self) -> int:
        return self._period + 1

    @property
    def params(self) -> dict:
        return {"period": self._period}


# ── VWAP Distance ────────────────────────────────────────────────────


@SignalRegistry.register("vwap_distance")
class VwapDistanceSignal(Signal):
    """Normalized distance from rolling VWAP.

    Positive = price above VWAP (bullish bias).
    Negative = price below VWAP (bearish bias).
    """

    def __init__(self, window: int = 96):
        self._window = window  # 96 bars @ 15m = 24h session

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        vwap = _rolling_vwap(data, self._window)
        distance = (data["close"] - vwap) / data["close"]
        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CONTINUOUS,
            values=distance,
            metadata={"vwap": vwap},
        )

    @property
    def lookback(self) -> int:
        return self._window

    @property
    def params(self) -> dict:
        return {"window": self._window}


# ── Absorption ────────────────────────────────────────────────────────


@SignalRegistry.register("absorption")
class AbsorptionSignal(Signal):
    """Detect absorption candles: high volume + tiny body.

    Absorption = large limit orders defending a level.
    """

    def __init__(self, vol_mult: float = 2.0, body_threshold: float = 0.3,
                 vol_period: int = 20):
        self._vol_mult = vol_mult
        self._body_threshold = body_threshold
        self._vol_period = vol_period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        vol_sma = data["volume"].rolling(self._vol_period).mean()
        vol_intensity = data["volume"] / vol_sma

        total_range = data["high"] - data["low"]
        body = (data["close"] - data["open"]).abs()
        # Avoid division by zero on doji bars.
        body_ratio = body / total_range.replace(0, np.nan)

        absorption = (
            (vol_intensity > self._vol_mult)
            & (body_ratio < self._body_threshold)
        ).astype(float)

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.BINARY,
            values=absorption,
        )

    @property
    def lookback(self) -> int:
        return self._vol_period

    @property
    def params(self) -> dict:
        return {
            "vol_mult": self._vol_mult,
            "body_threshold": self._body_threshold,
            "vol_period": self._vol_period,
        }


# ── Volume Regime ─────────────────────────────────────────────────────


@SignalRegistry.register("volume_regime")
class VolumeRegimeSignal(Signal):
    """Classify volume into LOW (-1), NORMAL (0), HIGH (1).

    Based on ratio of current volume to SMA(volume).
    """

    def __init__(self, period: int = 20, low_threshold: float = 0.7,
                 high_threshold: float = 1.5):
        self._period = period
        self._low = low_threshold
        self._high = high_threshold

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        vol_sma = data["volume"].rolling(self._period).mean()
        ratio = data["volume"] / vol_sma

        regime = pd.Series(0.0, index=data.index)
        regime[ratio < self._low] = -1.0
        regime[ratio > self._high] = 1.0

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CATEGORICAL,
            values=regime,
            metadata={"vol_ratio": ratio},
        )

    @property
    def lookback(self) -> int:
        return self._period

    @property
    def params(self) -> dict:
        return {
            "period": self._period,
            "low_threshold": self._low,
            "high_threshold": self._high,
        }


# ── Volume Threshold ──────────────────────────────────────────────────


@SignalRegistry.register("volume_threshold")
class VolumeThresholdSignal(Signal):
    """Binary gate: volume above multiplier × SMA(volume)."""

    def __init__(self, multiplier: float = 1.2, period: int = 20):
        self._multiplier = multiplier
        self._period = period

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        vol_sma = data["volume"].rolling(self._period).mean()
        gate = (data["volume"] > vol_sma * self._multiplier).astype(float)
        return SignalFrame(
            name=self.name,
            signal_type=SignalType.BINARY,
            values=gate,
        )

    @property
    def lookback(self) -> int:
        return self._period

    @property
    def params(self) -> dict:
        return {"multiplier": self._multiplier, "period": self._period}
