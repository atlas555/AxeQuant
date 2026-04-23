"""Parameter grid scanner for backtest optimisation.

Provides :func:`_run_single` (module-level for pickling) and
:class:`ParameterScanner` which drives sequential or multi-process
parameter sweeps over a base :class:`BacktestConfig`.
"""

from __future__ import annotations

import itertools
import logging
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Sequence

import pandas as pd

from app.services.backtestsys_plugin.config.loader import BacktestConfig, load_config
from app.services.backtestsys_plugin.core.utils import set_nested
from app.services.backtestsys_plugin.orchestrator.runner import BacktestRunner

logger = logging.getLogger(__name__)


# ── Helpers (module-level for pickling) ───────────────────────────────


def _set_nested(d: dict, dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted key path.

    .. deprecated:: Use :func:`backTestSys.core.utils.set_nested` instead.
    """
    set_nested(d, dotted_key, value)


def _run_single(args: tuple[dict, tuple, Sequence[str]]) -> dict:
    """Run a single backtest for one parameter combination.

    Must be a module-level function so :class:`ProcessPoolExecutor` can
    serialise it across processes.

    Parameters
    ----------
    args : tuple
        ``(config_dict, param_combo, param_names)`` where *config_dict*
        is a plain dict from ``BacktestConfig.model_dump()``,
        *param_combo* is one set of parameter values, and *param_names*
        are the corresponding dotted key names.

    Returns
    -------
    dict
        Merged dict of ``{param_name: value, ...} | metrics.to_dict()``.
    """
    config_dict, param_combo, param_names = args

    # Apply parameter overrides
    for name, val in zip(param_names, param_combo):
        set_nested(config_dict, name, val)

    cfg = BacktestConfig(**config_dict)
    runner = BacktestRunner()
    result = runner.run_config(cfg)

    row: dict[str, Any] = {name: val for name, val in zip(param_names, param_combo)}
    row.update(result.metrics.to_dict())
    return row


# ── Scanner ───────────────────────────────────────────────────────────


class ParameterScanner:
    """Sweep a parameter grid over a base backtest configuration.

    Usage::

        scanner = ParameterScanner()
        df = scanner.scan(
            "backtest.result/configs/btc_sma_cross.yaml",
            {"strategy.leverage": [1, 3, 5], "strategy.stop_loss_atr_mult": [1.5, 2.0]},
            n_workers=4,
        )
        print(df.sort_values("sharpe_ratio", ascending=False))
    """

    def scan(
        self,
        config_path: str,
        param_grid: dict[str, list],
        n_workers: int = 1,
    ) -> pd.DataFrame:
        """Run backtests for every combination in *param_grid*.

        Parameters
        ----------
        config_path : str
            Path to a YAML config file loadable by :func:`load_config`.
        param_grid : dict[str, list]
            Mapping of dotted parameter names to lists of values to try.
            Example: ``{"strategy.leverage": [1, 3], "strategy.stop_loss_atr_mult": [1.5, 2.0]}``
        n_workers : int
            Number of parallel workers.  ``1`` runs sequentially (good
            for debugging); ``>1`` uses :class:`ProcessPoolExecutor`.

        Returns
        -------
        pd.DataFrame
            One row per combination.  Columns = param names + all metric
            fields from :class:`MetricsReport`.
        """
        base_cfg = load_config(config_path)
        base_dict = base_cfg.model_dump()

        param_names = list(param_grid.keys())
        value_lists = [param_grid[k] for k in param_names]
        combos = list(itertools.product(*value_lists))

        logger.info(
            "Starting parameter scan: %d combos, %d workers",
            len(combos),
            n_workers,
        )

        # Build work items — each gets its own copy of the config dict
        import copy

        work_items = [
            (copy.deepcopy(base_dict), combo, param_names) for combo in combos
        ]

        rows: list[dict]
        if n_workers <= 1:
            rows = [_run_single(item) for item in work_items]
        else:
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                rows = list(pool.map(_run_single, work_items))

        df = pd.DataFrame(rows)
        logger.info("Parameter scan complete: %d rows", len(df))
        return df
