"""Market regime detector — classifies bars into 5 regimes.

Uses CVD (Cumulative Volume Delta), Volatility (ATR percentile), and
Trend (ADX/DMI) to classify each bar into one of:

    1 = STRONG_TREND   — CVD aligned with price, trending, vol moderate-high
    2 = EXHAUSTION      — CVD diverging from price, vol high
    3 = ACCUMULATION    — flat/ranging, CVD not falling, vol low
    4 = DISTRIBUTION    — flat/ranging, CVD falling, vol low
    5 = LIQUIDATION     — extreme vol + CVD spike

The regime classification adapts WaveTrend signal interpretation:
- STRONG_TREND:  pullback entries only
- EXHAUSTION:    OB/OS reversals valid
- ACCUMULATION:  zero-line crosses = breakout candidates
- DISTRIBUTION:  suppress signals, require divergence confirmation
- LIQUIDATION:   suspend all signals
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtestsys_plugin.signals.base import Signal, SignalFrame, SignalType
from app.services.backtestsys_plugin.signals.registry import SignalRegistry

# Regime constants (match Pine Script)
REGIME_STRONG_TREND = 1
REGIME_EXHAUSTION = 2
REGIME_ACCUMULATION = 3
REGIME_DISTRIBUTION = 4
REGIME_LIQUIDATION = 5


def _compute_delta(data: pd.DataFrame) -> pd.Series:
    """Per-bar volume delta (true taker or OHLCV proxy)."""
    if "taker_buy_volume" in data.columns:
        buy = data["taker_buy_volume"].astype(float)
        sell = data["volume"].astype(float) - buy
        return buy - sell
    sign = np.sign(data["close"] - data["open"])
    sign = sign.replace(0, np.sign(data["close"] - data["close"].shift(1)))
    sign = sign.replace(0, 1)
    return sign * data["volume"]


def _compute_adx(data: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute ADX, +DI, -DI using Wilder's smoothing."""
    high = data["high"]
    low = data["low"]
    close = data["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    plus_di = 100 * plus_dm_smooth / atr
    minus_di = 100 * minus_dm_smooth / atr

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    dx = dx.replace([np.inf, -np.inf], 0)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    return adx, plus_di, minus_di


def _percentile_rank(series: pd.Series, lookback: int) -> pd.Series:
    """Rolling percentile rank (0-100)."""
    def rank_fn(window):
        if len(window) < 2:
            return 50.0
        val = window.iloc[-1]
        return (window < val).sum() / (len(window) - 1) * 100
    return series.rolling(lookback, min_periods=2).apply(rank_fn, raw=False)


@SignalRegistry.register("regime_detector")
class RegimeDetectorSignal(Signal):
    """5-regime market classifier using CVD + Volatility + Trend."""

    def __init__(
        self,
        # CVD params
        cvd_len: int = 20,
        cvd_slope_len: int = 10,
        cvd_div_len: int = 20,
        cvd_extreme_mult: float = 3.0,
        # Volatility params
        atr_len: int = 14,
        atr_norm_len: int = 100,
        vol_low_pct: float = 25.0,
        vol_high_pct: float = 75.0,
        vol_extreme_pct: float = 95.0,
        # Trend params
        adx_len: int = 14,
        adx_trending: float = 25.0,
        price_slope_len: int = 10,
    ) -> None:
        self._cvd_len = cvd_len
        self._cvd_slope_len = cvd_slope_len
        self._cvd_div_len = cvd_div_len
        self._cvd_extreme_mult = cvd_extreme_mult
        self._atr_len = atr_len
        self._atr_norm_len = atr_norm_len
        self._vol_low_pct = vol_low_pct
        self._vol_high_pct = vol_high_pct
        self._vol_extreme_pct = vol_extreme_pct
        self._adx_len = adx_len
        self._adx_trending = adx_trending
        self._price_slope_len = price_slope_len

    def compute(self, data: pd.DataFrame) -> SignalFrame:
        n = len(data)

        # ── CVD ──────────────────────────────────────────────────
        delta = _compute_delta(data)
        cvd = delta.cumsum()
        cvd_ema = cvd.ewm(span=self._cvd_len, adjust=False).mean()

        sl = self._cvd_slope_len
        cvd_slope = (cvd_ema - cvd_ema.shift(sl)) / (cvd_ema.shift(sl).abs() + 1)
        cvd_rising = cvd_slope > 0.001
        cvd_falling = cvd_slope < -0.001

        # CVD divergence
        dl = self._cvd_div_len
        price_hh = data["close"].rolling(dl).max()
        price_ll = data["close"].rolling(dl).min()
        cvd_hh = cvd.rolling(dl).max()
        cvd_ll = cvd.rolling(dl).min()
        prev_price_hh = data["close"].shift(dl).rolling(dl).max()
        prev_price_ll = data["close"].shift(dl).rolling(dl).min()
        prev_cvd_hh = cvd.shift(dl).rolling(dl).max()
        prev_cvd_ll = cvd.shift(dl).rolling(dl).min()

        bearish_div = (price_hh > prev_price_hh) & (cvd_hh < prev_cvd_hh)
        bullish_div = (price_ll < prev_price_ll) & (cvd_ll > prev_cvd_ll)
        cvd_diverging = bearish_div | bullish_div

        # CVD extreme spike
        delta_std = delta.rolling(20).std()
        cvd_extreme_spike = delta.abs() > self._cvd_extreme_mult * delta_std

        # ── Volatility ───────────────────────────────────────────
        tr = pd.concat([
            data["high"] - data["low"],
            (data["high"] - data["close"].shift(1)).abs(),
            (data["low"] - data["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / self._atr_len, adjust=False).mean()
        atr_norm = atr / data["close"] * 100

        vol_rank = _percentile_rank(atr_norm, self._atr_norm_len)
        vol_low = vol_rank < self._vol_low_pct
        vol_high = vol_rank > self._vol_high_pct
        vol_extreme = vol_rank > self._vol_extreme_pct
        vol_normal = ~vol_low & ~vol_high

        # ── Trend ────────────────────────────────────────────────
        adx, plus_di, minus_di = _compute_adx(data, self._adx_len)
        trending = adx > self._adx_trending
        bull_trend = plus_di > minus_di

        psl = self._price_slope_len
        price_slope = (data["close"] - data["close"].shift(psl)) / data["close"].shift(psl) * 100
        price_rising = price_slope > 0.5
        price_falling = price_slope < -0.5

        cvd_aligned = (price_rising & cvd_rising) | (price_falling & cvd_falling)

        # ── Regime classification ────────────────────────────────
        regime = pd.Series(REGIME_DISTRIBUTION, index=data.index, dtype=float)

        is_liquidation = vol_extreme & cvd_extreme_spike
        is_strong_trend = trending & cvd_aligned & (vol_normal | vol_high) & ~vol_extreme
        is_exhaustion = cvd_diverging & vol_high & ~vol_extreme
        is_accumulation = ~trending & cvd_rising & vol_low
        is_distribution = ~trending & cvd_falling & vol_low

        # Priority: Liquidation > Strong Trend > Exhaustion > Accumulation > Distribution
        regime[is_distribution] = REGIME_DISTRIBUTION
        regime[is_accumulation] = REGIME_ACCUMULATION
        regime[is_exhaustion] = REGIME_EXHAUSTION
        regime[is_strong_trend] = REGIME_STRONG_TREND
        regime[is_liquidation] = REGIME_LIQUIDATION

        # Forward-fill to maintain regime hysteresis (carry forward when no clear signal)
        # Only fill NaN values, not intentional classifications
        regime = regime.ffill()

        return SignalFrame(
            name=self.name,
            signal_type=SignalType.CATEGORICAL,
            values=regime,
            metadata={
                "cvd": cvd,
                "cvd_slope": cvd_slope,
                "cvd_diverging": cvd_diverging.astype(float),
                "bullish_div": bullish_div.astype(float),
                "bearish_div": bearish_div.astype(float),
                "vol_rank": vol_rank,
                "adx": adx,
                "bull_trend": bull_trend.astype(float),
                "delta": delta,
            },
        )

    @property
    def lookback(self) -> int:
        return max(self._cvd_div_len * 3, self._atr_norm_len, self._adx_len * 3)

    @property
    def params(self) -> dict:
        return {
            "cvd_len": self._cvd_len,
            "cvd_slope_len": self._cvd_slope_len,
            "cvd_div_len": self._cvd_div_len,
            "cvd_extreme_mult": self._cvd_extreme_mult,
            "atr_len": self._atr_len,
            "atr_norm_len": self._atr_norm_len,
            "vol_low_pct": self._vol_low_pct,
            "vol_high_pct": self._vol_high_pct,
            "vol_extreme_pct": self._vol_extreme_pct,
            "adx_len": self._adx_len,
            "adx_trending": self._adx_trending,
            "price_slope_len": self._price_slope_len,
        }
