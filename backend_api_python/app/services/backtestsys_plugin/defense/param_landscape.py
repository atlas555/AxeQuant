"""Parameter Landscape diagnosis for backtest results.

Classifies the shape of a performance metric across parameter sweep runs
to detect overfitting signals (spike), edge effects (slope), healthy
robustness (plateau), or lack of edge (noise).
"""

import numpy as np
import pandas as pd


class ParameterLandscape:
    """Diagnose the parameter sensitivity landscape of backtest results."""

    def diagnose(self, results: pd.DataFrame, metric: str = "sharpe_ratio") -> str:
        """Classify the landscape shape of *metric* across parameter runs.

        Returns one of:
            "insufficient_data" - fewer than 3 data points
            "spike"   - single outlier peak, overfit risk (peak > 2σ from mean AND CV > 0.5)
            "slope"   - monotonically increasing or decreasing (edge effect)
            "plateau" - low variation, robust (CV < 0.3)
            "noise"   - no clear pattern, no edge
        """
        values = results[metric].dropna().values
        if len(values) < 3:
            return "insufficient_data"

        mean = np.mean(values)
        std = np.std(values, ddof=0)
        abs_mean = abs(mean) if mean != 0 else 1e-10
        cv = std / abs_mean

        # Spike: one value far from the rest (use median-based detection
        # to avoid the outlier inflating std and masking itself)
        median = np.median(values)
        mad = np.median(np.abs(values - median))
        mad_std = mad * 1.4826  # scale MAD to be comparable to std
        peak_dist_from_median = np.max(np.abs(values - median))
        if mad_std > 0 and peak_dist_from_median > 3 * mad_std and cv > 0.5:
            return "spike"

        # Slope: monotonically increasing or decreasing
        diffs = np.diff(values)
        if np.all(diffs > 0) or np.all(diffs < 0):
            return "slope"

        # Plateau: low CV means robust
        if cv < 0.3:
            return "plateau"

        return "noise"

    def positive_ratio(self, results: pd.DataFrame, metric: str = "sharpe_ratio") -> float:
        """Return fraction of rows where *metric* > 0."""
        values = results[metric].dropna().values
        if len(values) == 0:
            return 0.0
        return float(np.sum(values > 0) / len(values))
