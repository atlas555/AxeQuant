"""Walk-Forward Analysis (WFA) following Pardo (2008).

Splits data into sequential in-sample / out-of-sample windows, optimises
parameters on IS, validates on OOS, then stitches OOS equity curves to
measure robustness via the Walk-Forward Efficiency ratio.

Supports two stepping modes:
    * **anchored** – IS always starts at bar 0 (expanding window)
    * **rolling** – IS window slides with constant size
"""

from __future__ import annotations

import copy
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.services.backtestsys_plugin.config.loader import BacktestConfig, load_config
from app.services.backtestsys_plugin.core.utils import set_nested

logger = logging.getLogger(__name__)


# ── Config / Result dataclasses ───────────────────────────────────


@dataclass
class WFAConfig:
    """Configuration knobs for Walk-Forward Analysis."""

    is_oos_ratio: int = 4
    """Ratio of IS bars to OOS bars (e.g. 4 means IS is 4x OOS)."""

    min_is_bars: int = 1008
    """Minimum in-sample bars (default: 252 days of 4h bars)."""

    min_oos_bars: int = 252
    """Minimum out-of-sample bars."""

    step_mode: str = "anchored"
    """'anchored' (IS starts at 0) or 'rolling' (constant IS size)."""

    n_rounds: int = 6
    """Number of WFA rounds."""


@dataclass
class WFARound:
    """Results for a single WFA round."""

    round_idx: int
    is_range: range
    oos_range: range
    best_params: dict[str, Any]
    is_sharpe: float
    oos_sharpe: float
    oos_equity: list[float]


@dataclass
class WFAReport:
    """Aggregate WFA results across all rounds."""

    rounds: list[WFARound]
    combined_oos_equity: list[float]
    efficiency: float
    is_sharpes: list[float]
    oos_sharpes: list[float]

    @property
    def verdict(self) -> str:
        """Classify WFA outcome per Pardo thresholds."""
        if self.efficiency >= 0.5:
            return "HEALTHY"
        if self.efficiency >= 0.3:
            return "WARNING"
        return "OVERFITTING"


# ── Analyzer ──────────────────────────────────────────────────────


class WalkForwardAnalyzer:
    """Run Walk-Forward Analysis on a backtest configuration."""

    def __init__(self, config: WFAConfig | None = None) -> None:
        self.config = config or WFAConfig()

    # ── Window generation ─────────────────────────────────────────

    def _generate_windows(self, total_bars: int) -> list[tuple[range, range]]:
        """Compute (IS_range, OOS_range) pairs for all rounds.

        Parameters
        ----------
        total_bars : int
            Total number of bars in the dataset.

        Returns
        -------
        list[tuple[range, range]]
            Each element is ``(is_range, oos_range)`` where both are
            Python :class:`range` objects indexing into the bar array.
        """
        cfg = self.config
        ratio = cfg.is_oos_ratio
        n_rounds = cfg.n_rounds

        # Determine OOS window size: spread remaining bars after initial IS
        # across n_rounds.  For anchored: total = ratio*oos + n_rounds*oos
        oos_size = max(cfg.min_oos_bars, total_bars // (n_rounds + ratio))

        windows: list[tuple[range, range]] = []

        if cfg.step_mode == "anchored":
            # IS always starts at 0, grows each round
            is_base = max(cfg.min_is_bars, ratio * oos_size)
            for i in range(n_rounds):
                oos_start = is_base + i * oos_size
                oos_end = oos_start + oos_size
                if oos_end > total_bars:
                    break
                is_rng = range(0, oos_start)
                oos_rng = range(oos_start, oos_end)
                windows.append((is_rng, oos_rng))
        elif cfg.step_mode == "rolling":
            is_size = max(cfg.min_is_bars, ratio * oos_size)
            for i in range(n_rounds):
                is_start = i * oos_size
                is_end = is_start + is_size
                oos_start = is_end
                oos_end = oos_start + oos_size
                if oos_end > total_bars:
                    break
                is_rng = range(is_start, is_end)
                oos_rng = range(oos_start, oos_end)
                windows.append((is_rng, oos_rng))
        else:
            raise ValueError(f"Unknown step_mode: {cfg.step_mode!r}")

        return windows

    # ── Efficiency calculation ────────────────────────────────────

    @staticmethod
    def _calc_efficiency(
        is_sharpes: list[float], oos_sharpes: list[float]
    ) -> float:
        """Compute Walk-Forward Efficiency = mean(OOS Sharpe) / mean(IS Sharpe).

        Returns 0.0 if IS mean is zero or negative to avoid division errors.
        """
        if not is_sharpes or not oos_sharpes:
            return 0.0
        is_mean = sum(is_sharpes) / len(is_sharpes)
        oos_mean = sum(oos_sharpes) / len(oos_sharpes)
        if is_mean <= 0:
            return 0.0
        return oos_mean / is_mean

    # ── Main entry point ──────────────────────────────────────────

    def run(
        self,
        config_path: str,
        param_grid: dict[str, list],
        runner: object | None = None,
        scanner: object | None = None,
    ) -> WFAReport:
        """Execute Walk-Forward Analysis.

        Parameters
        ----------
        config_path : str
            Path to the base YAML config file.
        param_grid : dict[str, list]
            Parameter grid to optimise (same format as
            :meth:`ParameterScanner.scan`).
        runner : BacktestRunner | None
            Optional injected runner.  Defaults to a new
            :class:`~backTestSys.orchestrator.runner.BacktestRunner`.
        scanner : ParameterScanner | None
            Optional injected scanner.  Defaults to a new
            :class:`~backTestSys.orchestrator.scanner.ParameterScanner`.

        Returns
        -------
        WFAReport
            Aggregate results including per-round metrics, stitched OOS
            equity curve, and efficiency verdict.
        """
        if runner is None:
            from app.services.backtestsys_plugin.orchestrator.runner import BacktestRunner
            runner = BacktestRunner()
        if scanner is None:
            from app.services.backtestsys_plugin.orchestrator.scanner import ParameterScanner
            scanner = ParameterScanner()

        base_cfg = load_config(config_path)
        base_dict = base_cfg.model_dump()

        # Load full dataset to determine total bar count
        from app.services.backtestsys_plugin.data_io.data_loader import DataLoader

        loader = DataLoader(base_cfg.data.data_dir)
        full_data = loader.load(
            symbol=base_cfg.data.symbol,
            timeframe=base_cfg.data.timeframe,
            start=base_cfg.data.start,
            end=base_cfg.data.end,
        )
        total_bars = len(full_data)

        if isinstance(full_data.index, pd.DatetimeIndex):
            timestamps = full_data.index
        elif "open_time" in full_data.columns:
            timestamps = pd.to_datetime(full_data["open_time"])
        else:
            timestamps = pd.to_datetime(full_data.iloc[:, 0])

        windows = self._generate_windows(total_bars)
        if not windows:
            raise ValueError(
                f"Not enough bars ({total_bars}) to generate WFA windows"
            )

        rounds: list[WFARound] = []
        is_sharpes: list[float] = []
        oos_sharpes: list[float] = []
        combined_oos_equity: list[float] = []

        for round_idx, (is_rng, oos_rng) in enumerate(windows):
            logger.info(
                "WFA round %d/%d  IS=[%d:%d]  OOS=[%d:%d]",
                round_idx + 1,
                len(windows),
                is_rng.start,
                is_rng.stop,
                oos_rng.start,
                oos_rng.stop,
            )

            # ── IS optimisation ───────────────────────────────────
            is_dict = copy.deepcopy(base_dict)
            is_dict["data"]["start"] = str(timestamps[is_rng.start])
            is_dict["data"]["end"] = str(timestamps[is_rng.stop - 1])
            is_dict["defense"]["trial_logger"]["enabled"] = False

            # Write temp config for scanner (scanner expects a file path)
            import yaml

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as tmp:
                yaml.safe_dump(is_dict, tmp)
                tmp_path = tmp.name

            try:
                scan_df = scanner.scan(tmp_path, param_grid, n_workers=1)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

            if scan_df.empty:
                logger.warning("Round %d: scanner returned no results", round_idx)
                continue

            best_row = scan_df.sort_values("sharpe_ratio", ascending=False).iloc[0]
            best_is_sharpe = float(best_row["sharpe_ratio"])
            best_params = {
                k: best_row[k] for k in param_grid.keys() if k in best_row
            }

            # ── OOS validation ────────────────────────────────────
            oos_dict = copy.deepcopy(base_dict)
            oos_dict["data"]["start"] = str(timestamps[oos_rng.start])
            oos_dict["data"]["end"] = str(timestamps[oos_rng.stop - 1])
            oos_dict["defense"]["trial_logger"]["enabled"] = False

            # Apply best params to OOS config
            for k, v in best_params.items():
                set_nested(oos_dict, k, v)

            oos_cfg = BacktestConfig(**oos_dict)
            oos_result = runner.run_config(oos_cfg)
            oos_sharpe = float(oos_result.metrics.sharpe_ratio)

            rnd = WFARound(
                round_idx=round_idx,
                is_range=is_rng,
                oos_range=oos_rng,
                best_params=best_params,
                is_sharpe=best_is_sharpe,
                oos_sharpe=oos_sharpe,
                oos_equity=oos_result.equity_curve,
            )
            rounds.append(rnd)
            is_sharpes.append(best_is_sharpe)
            oos_sharpes.append(oos_sharpe)
            combined_oos_equity.extend(oos_result.equity_curve)

        efficiency = self._calc_efficiency(is_sharpes, oos_sharpes)

        report = WFAReport(
            rounds=rounds,
            combined_oos_equity=combined_oos_equity,
            efficiency=efficiency,
            is_sharpes=is_sharpes,
            oos_sharpes=oos_sharpes,
        )

        logger.info(
            "WFA complete: %d rounds, efficiency=%.3f, verdict=%s",
            len(rounds),
            efficiency,
            report.verdict,
        )
        return report
