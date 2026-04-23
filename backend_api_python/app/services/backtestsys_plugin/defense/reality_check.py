"""White's Reality Check (2000) and Hansen's SPA Test (2005).

Tests whether the best-performing strategy from a set of candidates
truly outperforms the benchmark after adjusting for data snooping.
"""
from __future__ import annotations

import numpy as np


class RealityCheck:
    """Multiple testing correction for strategy selection."""

    def whites_reality_check(
        self,
        benchmark_returns: np.ndarray,
        strategy_returns_list: list[np.ndarray],
        n_bootstrap: int = 10000,
    ) -> float:
        """White's Reality Check p-value.

        H0: No strategy beats the benchmark.
        Low p-value → reject H0 → best strategy has real alpha.
        """
        bm = np.asarray(benchmark_returns)
        n = len(bm)
        rng = np.random.default_rng(42)

        # Excess returns for each strategy
        excess = np.array([np.asarray(s) - bm for s in strategy_returns_list])  # (K, T)
        means = excess.mean(axis=1)
        observed_max = float(np.max(means))  # best average excess

        # Demean excess returns to impose H0 (no strategy beats benchmark)
        demeaned = excess - means[:, np.newaxis]

        # Stationary bootstrap (block bootstrap with geometric block length)
        block_len = max(1, int(np.sqrt(n)))
        count = 0
        for _ in range(n_bootstrap):
            # Block bootstrap resampling
            idx = self._block_bootstrap_indices(n, block_len, rng)
            boot_stat = float(np.max(demeaned[:, idx].mean(axis=1)))
            if boot_stat >= observed_max:
                count += 1

        return count / n_bootstrap

    def hansens_spa(
        self,
        benchmark_returns: np.ndarray,
        strategy_returns_list: list[np.ndarray],
        n_bootstrap: int = 10000,
    ) -> float:
        """Hansen's Superior Predictive Ability test.

        Improved version of White's RC — re-centres by demeaning ALL
        strategies to zero under H0, then only takes the max over
        strategies with non-negative sample mean (ignoring clearly
        inferior ones that would inflate the bootstrap critical value).
        """
        bm = np.asarray(benchmark_returns)
        n = len(bm)
        rng = np.random.default_rng(42)

        excess = np.array([np.asarray(s) - bm for s in strategy_returns_list])
        means = excess.mean(axis=1)

        # Observed statistic: max over ALL strategies
        observed_stat = float(np.max(means))

        # Re-centering: demean ALL strategies to zero mean under H0
        demeaned = excess - means[:, np.newaxis]

        # Mask: only consider strategies with non-negative sample mean
        # in the bootstrap distribution (ignore clearly inferior ones)
        mask = means >= 0

        block_len = max(1, int(np.sqrt(n)))
        count = 0
        for _ in range(n_bootstrap):
            idx = self._block_bootstrap_indices(n, block_len, rng)
            boot_means = demeaned[:, idx].mean(axis=1)
            if mask.any():
                boot_stat = float(np.max(boot_means[mask]))
            else:
                boot_stat = float(np.max(boot_means))
            if boot_stat >= observed_stat:
                count += 1

        return count / n_bootstrap

    @staticmethod
    def _block_bootstrap_indices(n: int, block_len: int, rng) -> np.ndarray:
        """Generate block bootstrap indices."""
        n_blocks = (n + block_len - 1) // block_len
        starts = rng.integers(0, n, size=n_blocks)
        indices = np.concatenate([np.arange(s, s + block_len) % n for s in starts])
        return indices[:n]
