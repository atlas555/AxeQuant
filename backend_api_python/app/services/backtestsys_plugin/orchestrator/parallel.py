"""Multi-asset parallel backtest runner.

Runs the same strategy across multiple symbols concurrently,
aggregating results into a comparison DataFrame.
"""
from __future__ import annotations

import copy
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

from app.services.backtestsys_plugin.config.loader import BacktestConfig, load_config
from app.services.backtestsys_plugin.orchestrator.runner import BacktestRunner


def _run_symbol(args: tuple) -> dict:
    """Worker: run backtest for a single symbol (must be top-level for pickling)."""
    config_dict, symbol = args
    config_dict["data"]["symbol"] = symbol
    config_dict["defense"]["trial_logger"]["enabled"] = False
    cfg = BacktestConfig(**config_dict)
    result = BacktestRunner().run_config(cfg)
    row = {"symbol": symbol}
    row.update(result.metrics.to_dict())
    row["n_bars"] = len(result.equity_curve)
    row["final_equity"] = result.equity_curve[-1] if result.equity_curve else 0
    return row


class ParallelRunner:
    """Run same strategy config across multiple symbols in parallel."""

    def run_multi(
        self,
        config_path: str,
        symbols: list[str],
        n_workers: int = -1,
    ) -> pd.DataFrame:
        """Run backtests for each symbol and return combined results.

        Parameters
        ----------
        config_path : str
            Base YAML config path (symbol will be overridden per run).
        symbols : list[str]
            List of symbol strings (e.g. ["BTCUSDT:USDT", "ETHUSDT:USDT"]).
        n_workers : int
            -1 = all CPUs, 1 = sequential.

        Returns
        -------
        pd.DataFrame with one row per symbol, columns = symbol + all metrics.
        """
        base_cfg = load_config(config_path)
        base_dict = base_cfg.model_dump()

        tasks = [(copy.deepcopy(base_dict), sym) for sym in symbols]

        if n_workers == 1:
            rows = [_run_symbol(t) for t in tasks]
        else:
            workers = None if n_workers == -1 else n_workers
            rows = []
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_run_symbol, t): t for t in tasks}
                for f in as_completed(futures):
                    rows.append(f.result())

        return pd.DataFrame(rows)
