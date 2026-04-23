"""Live monitor worker — kill-switch loop for active LiveRuns.

One coroutine per active live run. Reads equity + position from recent
snapshots; feeds metrics into KillSwitchMonitor; fires on sustained breach.

Runs in its own process (see docker-compose.override.yml bts_live_monitor).
"""

from __future__ import annotations

import asyncio
import logging
import os

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("bts.live_monitor_worker")


CHECK_INTERVAL = int(os.environ.get("LIVE_CHECK_INTERVAL", "300"))


async def monitor_run(run_id: str):
    from app.extensions import db
    from app.services.backtestsys_plugin.api.models import LiveRun
    from app.services.backtestsys_plugin.live.kill_switch import (
        KillSwitchMonitor, KillSwitchConfig, fire_kill_switch,
    )
    from app.services.backtestsys_plugin.live.notifications import send_urgent

    monitor = KillSwitchMonitor(KillSwitchConfig(check_interval_seconds=CHECK_INTERVAL))

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        run = LiveRun.query.filter_by(id=run_id).first()
        if run is None or run.status in ("killed", "stopped", "failed"):
            log.info("live monitor exit for %s (status=%s)", run_id,
                     getattr(run, "status", "missing"))
            break

        drift_pct, dd_ratio = _compute_metrics(run_id, db.session)

        should_fire = monitor.tick(drift_pct=drift_pct, dd_ratio=dd_ratio)
        if should_fire:
            from app.services.live_trading.factory import get_exchange
            exchange = get_exchange(run.exchange, testnet=False)
            reason = monitor.reason()
            await fire_kill_switch(run, exchange, reason,
                                   db_session=db.session, alert_fn=send_urgent)
            break

        # Also honor manual kill requests
        if run.status == "killing":
            from app.services.live_trading.factory import get_exchange
            exchange = get_exchange(run.exchange, testnet=False)
            await fire_kill_switch(run, exchange, run.kill_reason or "manual",
                                   db_session=db.session, alert_fn=send_urgent)
            break


def _compute_metrics(run_id: str, db_session) -> tuple[float, float]:
    """Compare live equity vs backtest reference; returns (drift_pct, dd_ratio).

    Stubbed to conservative defaults — replace with real computation once
    live snapshots + offline replay are wired. Current behavior: never trips
    the switch based on drift/DD (relying on manual-kill only).
    """
    return 0.0, 0.0


def main():
    from app import create_app
    from app.services.backtestsys_plugin.api.models import LiveRun

    app = create_app()

    async def outer():
        active: dict[str, asyncio.Task] = {}
        while True:
            with app.app_context():
                rows = LiveRun.query.filter(
                    LiveRun.status.in_(("running", "starting", "killing"))
                ).all()
                for r in rows:
                    if r.id not in active or active[r.id].done():
                        active[r.id] = asyncio.create_task(monitor_run(r.id))
            # Garbage-collect finished tasks
            active = {k: v for k, v in active.items() if not v.done()}
            await asyncio.sleep(60)

    with app.app_context():
        asyncio.run(outer())


if __name__ == "__main__":
    main()
