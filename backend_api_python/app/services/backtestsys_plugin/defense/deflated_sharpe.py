"""Deflated Sharpe Ratio — Bailey & López de Prado (2014).

Adjusts the observed Sharpe ratio for the number of trials (strategies)
tested, accounting for skewness, kurtosis, and multiple-testing bias.

DSR > 0.95 → confident the result is not overfit.
DSR < 0.50 → likely overfit.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike
from scipy.stats import kurtosis as sp_kurtosis
from scipy.stats import norm, skew as sp_skew

# Euler-Mascheroni constant
_GAMMA = 0.5772156649015329


class DeflatedSharpeRatio:
    """Compute the Deflated Sharpe Ratio for a backtest result."""

    # ── Public API ────────────────────────────────────────────────

    def compute(
        self,
        observed_sharpe: float,
        n_trials: int,
        returns: ArrayLike,
    ) -> float:
        """Return DSR in [0, 1].

        Parameters
        ----------
        observed_sharpe : float
            Sharpe ratio of the selected (best) strategy.
        n_trials : int
            Total number of strategies / parameter combos tested.
        returns : array-like
            Strategy return series used to estimate skewness & kurtosis.
        """
        returns = np.asarray(returns, dtype=float)
        T = len(returns)

        # Edge-case guards
        if n_trials < 1 or T < 3:
            return 0.0

        sr_star = self._expected_max_sr(n_trials, T)
        return self._psr(observed_sharpe, sr_star, returns)

    # ── Expected maximum Sharpe under the null ────────────────────

    def _expected_max_sr(self, n_trials: int, n_obs: int) -> float:
        """E[max SR] assuming all strategies have true SR = 0.

        Uses the approximation from Bailey & López de Prado (2014):
            z1 = Φ⁻¹(1 - 1/N)
            z2 = Φ⁻¹(1 - 1/(N·e))
            SR* = (1-γ)·z1 + γ·z2

        The SR estimator standard deviation (1/sqrt(T)) is already
        accounted for in the PSR denominator, so it is not applied here.
        """
        N = max(n_trials, 1)

        z1 = norm.ppf(1.0 - 1.0 / N)
        z2 = norm.ppf(1.0 - 1.0 / (N * math.e))

        return float((1.0 - _GAMMA) * z1 + _GAMMA * z2)

    # ── Probabilistic Sharpe Ratio ────────────────────────────────

    def _psr(
        self,
        observed_sr: float,
        sr_star: float,
        returns: np.ndarray,
    ) -> float:
        """Probabilistic Sharpe Ratio with skewness/kurtosis adjustment.

        z = (SR - SR*) · sqrt(T-1) / sqrt(1 - skew·SR + (kurt-1)/4 · SR²)
        PSR = Φ(z)
        """
        T = len(returns)
        s = float(sp_skew(returns))
        k = float(sp_kurtosis(returns, fisher=False))  # raw (excess=False)

        sr = observed_sr

        # Guard against NaN from constant returns (zero variance)
        if math.isnan(s) or math.isnan(k):
            return 0.0

        denom_sq = 1.0 - s * sr + (k - 1.0) / 4.0 * sr * sr

        if denom_sq <= 0.0:
            return 0.0

        z = (sr - sr_star) * math.sqrt(T - 1) / math.sqrt(denom_sq)
        return float(norm.cdf(z))
