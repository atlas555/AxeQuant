"""Autoresearch worker — consumes bts:autoresearch:jobs."""

from __future__ import annotations

import logging
import os

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("bts.autoresearch_worker")


def _default_metric_factory(base_config: dict):
    """Build a metric_fn that runs a backTestSys backtest and returns OOS Sharpe.

    This uses the vendored BacktestRunner. Requires `data_dir` in base_config
    to point to a readable CSV tree (the plugin's data_io/ loader).
    """
    from app.services.backtestsys_plugin.orchestrator.runner import BacktestRunner

    def metric(cfg: dict) -> float:
        runner = BacktestRunner()
        try:
            result = runner.run_from_dict(cfg)  # see runner API
            return float(getattr(result, "oos_sharpe", result.metrics.sharpe))
        except Exception:
            log.exception("Backtest failed for cfg, scoring as -inf")
            return float("-inf")

    return metric


def main():
    from app import create_app
    from app.extensions import db
    from app.services.backtestsys_plugin.api.autoresearch_service import process_autoresearch_job
    from app.services.backtestsys_plugin.api.common import (
        AUTORESEARCH_QUEUE, run_worker_loop,
    )

    app = create_app()
    with app.app_context():
        def handler(job_id: str):
            process_autoresearch_job(
                job_id, db_session=db.session,
                metric_fn_factory=_default_metric_factory,
            )
        run_worker_loop(AUTORESEARCH_QUEUE, handler)


if __name__ == "__main__":
    main()
