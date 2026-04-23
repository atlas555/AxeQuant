"""Structure optimizer — layered coordinate descent with anti-overfit guard.

Optimizes structural choices (Layer 1) before sizing (Layer 2), then
optionally fine-tunes signal params (Layer 0) in a narrow neighborhood.

Algorithm: Coordinate Descent
  For each layer in order:
    For each param in the layer:
      Generate grid -> test each -> keep best if improves
    Repeat layer if any param improved (interaction effects)
  Stop when no improvement in a full pass, or max_iterations reached.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable

from app.services.backtestsys_plugin.optimizer.param_spec import ParamSpec, StrategyParams
from app.services.backtestsys_plugin.optimizer.report import OptimizationReport, OptResult

logger = logging.getLogger(__name__)

MetricFn = Callable[[dict[str, Any]], float]
RunFn = Callable[[dict[str, Any], str, str], float]  # (cfg, start, end) -> Sharpe


class StructureOptimizer:
    """Generic layered coordinate-descent optimizer.

    Usage::

        opt = StructureOptimizer(params, metric_fn)
        report = opt.optimize(layers=[1, 2])
        wfa = opt.validate_wfa(run_fn, periods)
    """

    def __init__(
        self,
        params: StrategyParams,
        metric_fn: MetricFn,
        maximize: bool = True,
    ) -> None:
        self.params = params.clone()
        self.metric_fn = metric_fn
        self.maximize = maximize
        self._best_metric: float | None = None
        self._log: list[OptResult] = []
        self._iter: int = 0

    def _is_better(self, new: float, old: float) -> bool:
        return new > old if self.maximize else new < old

    def _run_metric(self, desc: str) -> OptResult:
        """Run metric on current params and return result."""
        cfg = self.params.to_dict()
        self._iter += 1
        t0 = time.time()
        m = self.metric_fn(cfg)
        elapsed = time.time() - t0
        result = OptResult(
            iteration=self._iter,
            metric=m,
            config=copy.deepcopy(cfg),
            desc=desc,
            keep=False,
            elapsed=elapsed,
        )
        return result

    def _run_with_value(self, name: str, value: Any, desc: str) -> OptResult:
        """Set param, run metric, restore."""
        old = self.params.get(name)
        self.params.set(name, value)
        result = self._run_metric(desc)
        self.params.set(name, old)  # restore
        return result

    def grid_search(self, name: str, values: list | None = None) -> list[OptResult]:
        """Test a parameter across values, return results sorted by metric."""
        spec = self.params.get_spec(name)
        if values is None:
            values = spec.get_grid()

        results = []
        for v in values:
            if not spec.validate(v):
                continue
            r = self._run_with_value(name, v, f"{name}={v}")
            results.append(r)
            logger.info("  %s=%s: metric=%.4f", name, v, r.metric)

        results.sort(key=lambda r: r.metric, reverse=self.maximize)
        return results

    def _optimize_param(self, spec: ParamSpec, neighborhood: bool = False) -> bool:
        """Optimize a single param. Returns True if improved."""
        if neighborhood:
            values = spec.get_neighborhood(self.params.get(spec.name), n_steps=1)
        else:
            values = spec.get_grid()

        if len(values) <= 1:
            return False

        results = self.grid_search(spec.name, values)
        if not results:
            return False

        best = results[0]
        if self._best_metric is None or self._is_better(best.metric, self._best_metric):
            self.params.set(spec.name, best.config[spec.name])
            best.keep = True
            self._best_metric = best.metric
            self._log.append(best)
            logger.info("  >>> KEEP %s=%s metric=%.4f",
                        spec.name, best.config[spec.name], best.metric)
            return True
        else:
            best.keep = False
            self._log.append(best)
            logger.info("  >>> DISCARD %s (best=%.4f, current=%.4f)",
                        spec.name, best.metric, self._best_metric)
            return False

    def optimize(
        self,
        layers: list[int] | None = None,
        max_iterations: int = 100,
        skip_danger: bool = True,
        max_rounds: int = 3,
    ) -> OptimizationReport:
        """Run layered coordinate descent optimization.

        Args:
            layers: Which layers to optimize, in order. Default [1, 2, 0].
            max_iterations: Total iteration budget.
            skip_danger: Skip params with danger=True.
            max_rounds: Max passes over all layers before stopping.

        Returns:
            OptimizationReport with full log.
        """
        if layers is None:
            layers = [1, 2, 0]

        # Baseline
        baseline_result = self._run_metric("baseline")
        self._best_metric = baseline_result.metric
        baseline_result.keep = True
        self._log.append(baseline_result)
        baseline_metric = baseline_result.metric
        logger.info("Baseline: %.4f", baseline_metric)

        for round_n in range(max_rounds):
            any_improved = False
            logger.info("\n=== Round %d/%d ===", round_n + 1, max_rounds)

            for layer in layers:
                label = {0: "Signal", 1: "Structure", 2: "Sizing"}.get(layer, f"L{layer}")
                logger.info("\n--- Layer %d (%s) ---", layer, label)

                specs = self.params.get_layer(layer)
                for spec in specs:
                    if self._iter >= max_iterations:
                        logger.info("Reached max iterations (%d)", max_iterations)
                        break

                    if skip_danger and spec.danger:
                        logger.info("  Skipping %s (danger)", spec.name)
                        continue

                    # Layer 0 uses neighborhood, others use full grid
                    neighborhood = (layer == 0)
                    if self._optimize_param(spec, neighborhood=neighborhood):
                        any_improved = True

                if self._iter >= max_iterations:
                    break

            if not any_improved:
                logger.info("Converged - no improvement in full pass")
                break

            if self._iter >= max_iterations:
                break

        return OptimizationReport(
            baseline_metric=baseline_metric,
            final_metric=self._best_metric,
            iterations=self._iter,
            log=list(self._log),
            final_config=self.params.to_dict(),
        )

    def validate_wfa(
        self,
        run_fn: RunFn,
        periods: list[tuple[str, str, str, str]],
    ) -> dict:
        """Run Walk-Forward Analysis on current best config.

        Args:
            run_fn: (config_dict, start_date, end_date) -> Sharpe
            periods: List of (is_start, is_end, oos_start, oos_end) tuples.

        Returns:
            Dict with is_sharpes, oos_sharpes, efficiency, verdict.
        """
        cfg = self.params.to_dict()
        is_sharpes = []
        oos_sharpes = []

        for is_s, is_e, oos_s, oos_e in periods:
            is_sr = run_fn(cfg, is_s, is_e)
            oos_sr = run_fn(cfg, oos_s, oos_e)
            is_sharpes.append(is_sr)
            oos_sharpes.append(oos_sr)
            logger.info("  WFA: IS=[%s..%s] %.3f  OOS=[%s..%s] %.3f",
                        is_s, is_e, is_sr, oos_s, oos_e, oos_sr)

        is_mean = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0
        oos_mean = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0
        efficiency = oos_mean / is_mean if is_mean > 0 else 0

        if efficiency >= 0.5:
            verdict = "HEALTHY"
        elif efficiency >= 0.3:
            verdict = "WARNING"
        else:
            verdict = "OVERFITTING"

        return {
            "is_sharpes": is_sharpes,
            "oos_sharpes": oos_sharpes,
            "is_mean": is_mean,
            "oos_mean": oos_mean,
            "efficiency": efficiency,
            "verdict": verdict,
            "periods": periods,
        }
