"""Kill switch — monitors a live run and auto-flattens on breach.

Monitor = stateful per-run checker. Breach must be sustained for
`consecutive_breaches_required` ticks to fire (prevents flapping on single
noisy reads).

Firing = flip run.status to 'killed', submit market-close orders to flatten
all positions, record an audit event, send urgent notification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class KillSwitchConfig:
    max_drift_pct: float = 40.0
    max_dd_multiplier: float = 2.0
    consecutive_breaches_required: int = 2
    check_interval_seconds: int = 300


@dataclass
class KillSwitchState:
    breach_count: int = 0
    fired: bool = False
    last_drift_pct: float = 0.0
    last_dd_ratio: float = 0.0


class KillSwitchMonitor:
    """Pure state machine. Caller feeds metrics; monitor returns decisions."""

    def __init__(self, cfg: KillSwitchConfig | None = None):
        self.cfg = cfg or KillSwitchConfig()
        self.state = KillSwitchState()

    def tick(self, *, drift_pct: float, dd_ratio: float) -> bool:
        """Ingest one observation. Returns True if kill should fire now."""
        self.state.last_drift_pct = drift_pct
        self.state.last_dd_ratio = dd_ratio

        breach = (
            drift_pct > self.cfg.max_drift_pct or
            dd_ratio > self.cfg.max_dd_multiplier
        )
        if breach:
            self.state.breach_count += 1
        else:
            self.state.breach_count = 0

        if (
            not self.state.fired
            and self.state.breach_count >= self.cfg.consecutive_breaches_required
        ):
            self.state.fired = True
            return True
        return False

    def reason(self) -> str:
        parts = []
        if self.state.last_drift_pct > self.cfg.max_drift_pct:
            parts.append(
                f"drift {self.state.last_drift_pct:.1f}% > {self.cfg.max_drift_pct}%"
            )
        if self.state.last_dd_ratio > self.cfg.max_dd_multiplier:
            parts.append(
                f"DD ratio {self.state.last_dd_ratio:.2f} > {self.cfg.max_dd_multiplier}"
            )
        return "; ".join(parts) if parts else "threshold breach"


async def fire_kill_switch(run, exchange, reason: str, db_session,
                           alert_fn: Callable | None = None) -> None:
    """Flatten positions, mark run killed, log + alert.

    Idempotent — safe to call twice.
    """
    from app.services.backtestsys_plugin.api.common import utcnow
    from app.services.backtestsys_plugin.live.audit_log import log_event
    from app.services.backtestsys_plugin.live.signal_to_order import translate_close

    if run.status == "killed":
        log.warning("kill_switch.fire called on already-killed run %s", run.id)
        return

    run.status = "killed"
    run.killed_at = utcnow()
    run.kill_reason = reason
    db_session.commit()

    log_event(run.id, "kill_switch_fired", {"reason": reason}, db_session)

    # Flatten via exchange
    try:
        positions = await _maybe_await(
            exchange.fetch_positions([run.config["symbol"]])
            if hasattr(exchange, "fetch_positions") else []
        )
        for pos in positions or []:
            size = float(pos.get("contracts", 0) or pos.get("size", 0) or 0)
            side = "long" if float(pos.get("side") == "long" or pos.get("contracts", 0) > 0) else "short"
            kwargs = translate_close({"size": size, "side": side}, run.config["symbol"])
            if kwargs:
                await _maybe_await(exchange.create_order(**kwargs))
                log_event(run.id, "position_flattened_by_kill", kwargs, db_session)
    except Exception as e:  # noqa: BLE001
        log.exception("kill-switch flatten failed for %s", run.id)
        log_event(run.id, "kill_switch_flatten_failed", {"error": str(e)}, db_session)

    if alert_fn is not None:
        try:
            alert_fn(run, reason)
        except Exception:  # noqa: BLE001
            log.exception("kill-switch alert failed")


async def _maybe_await(obj):
    import asyncio
    if asyncio.iscoroutine(obj):
        return await obj
    return obj
