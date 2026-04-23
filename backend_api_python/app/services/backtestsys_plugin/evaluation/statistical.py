"""Statistical tests for backtest robustness evaluation.

Provides :class:`StatisticalTests` with bootstrap confidence intervals,
permutation tests, and block bootstrap for autocorrelated returns.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike


class StatisticalTests:
    """Stateless statistical testing utilities for return series."""

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _annualized_sharpe(returns: np.ndarray, bars_per_year: int) -> float:
        """Compute annualized Sharpe ratio. Returns 0.0 when std == 0."""
        if len(returns) == 0:
            return 0.0
        std = returns.std(ddof=1) if len(returns) > 1 else 0.0
        if std == 0.0:
            return 0.0
        return float(returns.mean() / std * np.sqrt(bars_per_year))

    # ── public API ───────────────────────────────────────────────────

    @staticmethod
    def bootstrap_sharpe_ci(
        returns: ArrayLike,
        n_bootstrap: int = 10_000,
        ci: float = 0.95,
        bars_per_year: int = 8760,
    ) -> tuple[float, float]:
        """Bootstrap confidence interval for annualized Sharpe ratio.

        Resamples *returns* with replacement *n_bootstrap* times, computes
        the annualized Sharpe for each sample, and returns the (*lower*,
        *upper*) percentile boundaries.

        Uses ``np.random.default_rng(42)`` for reproducibility.
        """
        returns = np.asarray(returns, dtype=float)
        rng = np.random.default_rng(42)
        n = len(returns)

        sharpes = np.empty(n_bootstrap)
        for i in range(n_bootstrap):
            sample = rng.choice(returns, size=n, replace=True)
            sharpes[i] = StatisticalTests._annualized_sharpe(sample, bars_per_year)

        alpha = (1.0 - ci) / 2.0
        lower = float(np.percentile(sharpes, 100 * alpha))
        upper = float(np.percentile(sharpes, 100 * (1.0 - alpha)))
        return (lower, upper)

    @staticmethod
    def permutation_test(
        returns: ArrayLike,
        n_perms: int = 10_000,
        bars_per_year: int = 8760,
    ) -> float:
        """Permutation test p-value for annualized Sharpe ratio.

        Computes the observed annualized Sharpe, then randomly flips the
        signs of returns *n_perms* times (sign-randomization test).
        Returns the fraction of permuted Sharpes >= the observed value.

        Sign-randomization tests the null hypothesis that the mean return
        is zero, which is the standard permutation approach for Sharpe.

        Uses ``np.random.default_rng(42)`` for reproducibility.
        """
        returns = np.asarray(returns, dtype=float)
        rng = np.random.default_rng(42)

        observed = StatisticalTests._annualized_sharpe(returns, bars_per_year)

        count = 0
        for _ in range(n_perms):
            signs = rng.choice([-1.0, 1.0], size=len(returns))
            permuted = returns * signs
            if StatisticalTests._annualized_sharpe(permuted, bars_per_year) >= observed:
                count += 1

        return count / n_perms

    @staticmethod
    def block_bootstrap_ci(
        returns: ArrayLike,
        block_size: int = 20,
        n_bootstrap: int = 10_000,
        ci: float = 0.95,
        bars_per_year: int = 8760,
    ) -> tuple[float, float]:
        """Block bootstrap CI for annualized Sharpe (handles autocorrelation).

        Instead of resampling individual observations, resamples contiguous
        blocks of size *block_size*.  Random start indices are drawn, blocks
        are concatenated, and the result is trimmed to the original length.

        Uses ``np.random.default_rng(42)`` for reproducibility.
        """
        returns = np.asarray(returns, dtype=float)
        rng = np.random.default_rng(42)
        n = len(returns)
        n_blocks = int(np.ceil(n / block_size))

        sharpes = np.empty(n_bootstrap)
        for i in range(n_bootstrap):
            starts = rng.integers(0, n - block_size + 1, size=n_blocks)
            blocks = [returns[s : s + block_size] for s in starts]
            sample = np.concatenate(blocks)[:n]
            sharpes[i] = StatisticalTests._annualized_sharpe(sample, bars_per_year)

        alpha = (1.0 - ci) / 2.0
        lower = float(np.percentile(sharpes, 100 * alpha))
        upper = float(np.percentile(sharpes, 100 * (1.0 - alpha)))
        return (lower, upper)
