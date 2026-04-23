"""Combinatorial Purged Cross-Validation (de Prado 2018, AFML Ch. 12).

Generates C(N, k) train/test splits with purging and embargo to prevent
information leakage in financial time series.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

import numpy as np


@dataclass
class CPCVConfig:
    n_groups: int = 6       # split data into N groups
    k_test: int = 2         # select k groups as test set
    purge_bars: int = 5     # bars to remove before test boundaries
    embargo_bars: int = 3   # bars to remove after test boundaries


@dataclass
class CPCVReport:
    n_paths: int
    oos_sharpes: list[float]
    oos_returns: list[float]
    mean_oos_sharpe: float
    std_oos_sharpe: float

    @property
    def pct_positive_sharpe(self) -> float:
        if not self.oos_sharpes:
            return 0.0
        return sum(1 for s in self.oos_sharpes if s > 0) / len(self.oos_sharpes)

    @property
    def verdict(self) -> str:
        if self.pct_positive_sharpe >= 0.8 and self.mean_oos_sharpe > 0:
            return "ROBUST"
        if self.pct_positive_sharpe >= 0.5:
            return "MARGINAL"
        return "LIKELY_OVERFIT"


class CPCVAnalyzer:
    """Runs CPCV with purge + embargo on a backtest config."""

    def __init__(self, config: CPCVConfig | None = None):
        self.config = config or CPCVConfig()

    def _generate_splits(self, total_bars: int) -> list[tuple[list[int], list[int]]]:
        """Generate all C(N, k) purged+embargoed train/test index splits."""
        cfg = self.config
        group_size = total_bars // cfg.n_groups
        groups = []
        for i in range(cfg.n_groups):
            start = i * group_size
            end = start + group_size if i < cfg.n_groups - 1 else total_bars
            groups.append(list(range(start, end)))

        splits = []
        for test_combo in combinations(range(cfg.n_groups), cfg.k_test):
            test_idx = []
            for g in test_combo:
                test_idx.extend(groups[g])
            test_set = set(test_idx)
            test_min, test_max = min(test_idx), max(test_idx)

            # Purge: remove bars before each test group start
            purge_set = set()
            for g in test_combo:
                g_start = groups[g][0]
                for i in range(max(0, g_start - cfg.purge_bars), g_start):
                    purge_set.add(i)

            # Embargo: remove bars after each test group end
            embargo_set = set()
            for g in test_combo:
                g_end = groups[g][-1]
                for i in range(g_end + 1, min(g_end + 1 + cfg.embargo_bars, total_bars)):
                    embargo_set.add(i)

            train_idx = [
                i for i in range(total_bars)
                if i not in test_set and i not in purge_set and i not in embargo_set
            ]
            splits.append((train_idx, test_idx))

        return splits

    def run(
        self,
        config_path: str,
        param_grid: dict[str, list],
        runner: object | None = None,
    ) -> CPCVReport:
        """Run CPCV: for each split, optimize on train, evaluate on test.

        Uses index-based DataFrame slicing so that purge/embargo gaps are
        correctly excluded.  Previous implementation converted indices to
        date strings and re-loaded data, which re-included purged bars.

        Parameters
        ----------
        runner : BacktestRunner | None
            Optional injected runner.  Defaults to a new
            :class:`~backTestSys.orchestrator.runner.BacktestRunner`.

        Returns distribution of OOS Sharpe ratios across all C(N,k) paths.
        """
        if runner is None:
            from app.services.backtestsys_plugin.orchestrator.runner import BacktestRunner
            runner = BacktestRunner()

        from app.services.backtestsys_plugin.config.loader import load_config, BacktestConfig
        from app.services.backtestsys_plugin.core.utils import set_nested
        from app.services.backtestsys_plugin.data_io.data_loader import DataLoader
        import copy
        from itertools import product

        base_cfg = load_config(config_path)
        loader = DataLoader(base_cfg.data.data_dir)
        full_data = loader.load(base_cfg.data.symbol, base_cfg.data.timeframe,
                                base_cfg.data.start, base_cfg.data.end)

        splits = self._generate_splits(len(full_data))
        base_dict = base_cfg.model_dump()
        base_dict["defense"]["trial_logger"]["enabled"] = False

        param_names = list(param_grid.keys())
        value_lists = [param_grid[k] for k in param_names]
        all_combos = list(product(*value_lists))

        oos_sharpes = []
        oos_returns = []

        for train_idx, test_idx in splits:
            # Slice DataFrame by exact indices (preserves purge/embargo gaps)
            train_data = full_data.iloc[train_idx].copy()
            test_data = full_data.iloc[test_idx].copy()

            # IS: sweep param grid on train_data using run_with_data
            best_sharpe = -float('inf')
            best_params: dict[str, object] = {}
            for combo in all_combos:
                is_dict = copy.deepcopy(base_dict)
                for k, v in zip(param_names, combo):
                    set_nested(is_dict, k, v)
                is_cfg = BacktestConfig(**is_dict)
                result = runner.run_with_data(is_cfg, train_data)
                if result.metrics.sharpe_ratio > best_sharpe:
                    best_sharpe = result.metrics.sharpe_ratio
                    best_params = dict(zip(param_names, combo))

            # OOS: run with best params on test_data
            oos_dict = copy.deepcopy(base_dict)
            for k, v in best_params.items():
                set_nested(oos_dict, k, v)
            oos_cfg = BacktestConfig(**oos_dict)
            oos_result = runner.run_with_data(oos_cfg, test_data)
            oos_sharpes.append(oos_result.metrics.sharpe_ratio)
            oos_returns.append(oos_result.metrics.total_return)

        return CPCVReport(
            n_paths=len(splits),
            oos_sharpes=oos_sharpes,
            oos_returns=oos_returns,
            mean_oos_sharpe=float(np.mean(oos_sharpes)) if oos_sharpes else 0.0,
            std_oos_sharpe=float(np.std(oos_sharpes)) if oos_sharpes else 0.0,
        )
