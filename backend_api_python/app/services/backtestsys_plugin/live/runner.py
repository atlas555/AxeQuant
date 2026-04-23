"""Live/paper strategy runner — bar-by-bar bridge between ScriptStrategy + CCXT.

Architecture:
    exchange.watch_ohlcv(symbol, timeframe)  →  LivePaperContext
                                                  ↓
                                          compiled on_bar(ctx)
                                                  ↓
                                    ctx._orders queue → translate_action
                                                  ↓
                                        exchange.create_order(...)

The runner is driven asynchronously (CCXT v4 uses asyncio). It reads the
control column on `bts_paper_runs.status` to decide when to stop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class LivePaperContext:
    """Extends QD's StrategyScriptContext with live-order semantics.

    Kept lazy-constructed so we can initialize without a running Flask app
    (for testing the bar-processing loop in isolation).
    """

    def __init__(self, bars_df, initial_balance: float, symbol: str,
                 exchange, run_id: str):
        self._bars_df = bars_df
        self.current_index = -1
        self._orders: list[dict] = []
        self._logs: list[str] = []
        self._params: dict = {}
        self.position: dict = {"size": 0.0, "side": None, "avg_price": 0.0}
        self.balance = float(initial_balance)
        self.equity = float(initial_balance)
        self.symbol = symbol
        self._exchange = exchange
        self.run_id = run_id

        # Install ctx.signal() from the adapters layer
        from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals
        attach_signals(self)

    # ── ScriptStrategy surface (mirrors StrategyScriptContext) ──────

    def param(self, name: str, default: Any = None) -> Any:
        if name not in self._params:
            self._params[name] = default
        return self._params[name]

    def bars(self, n: int = 1):
        start = max(0, self.current_index - int(n) + 1)
        out = []
        for _, row in self._bars_df.iloc[start:self.current_index + 1].iterrows():
            out.append({
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
                "timestamp": row.get("time") or row.get("timestamp"),
            })
        return out

    def log(self, msg: Any):
        self._logs.append(str(msg))

    def buy(self, price=None, amount=None):
        self._orders.append({"action": "buy", "price": price, "amount": amount})

    def sell(self, price=None, amount=None):
        self._orders.append({"action": "sell", "price": price, "amount": amount})

    def close_position(self):
        self._orders.append({"action": "close"})

    # ── Order submission (overridable for tests) ────────────────────

    async def flush_orders(self):
        """Submit queued orders to the exchange and clear the queue."""
        from app.services.backtestsys_plugin.live.signal_to_order import (
            translate_action, translate_close,
        )
        pending = self._orders
        self._orders = []
        for action in pending:
            if action["action"] == "close":
                kwargs = translate_close(self.position, self.symbol)
                if kwargs is None:
                    continue
            else:
                kwargs = translate_action(action, self.symbol)
            log.info("[%s] submit order: %s", self.run_id, kwargs)
            try:
                result = await _maybe_await(self._exchange.create_order(**kwargs))
                log.info("[%s] order result: %s", self.run_id, result)
            except Exception:  # noqa: BLE001
                log.exception("[%s] order submission failed: %s", self.run_id, kwargs)


async def _maybe_await(obj):
    if asyncio.iscoroutine(obj):
        return await obj
    return obj


async def run_session(ctx: LivePaperContext, on_init, on_bar,
                      bar_stream, is_running) -> None:
    """Drive the strategy bar-by-bar until `is_running()` returns False.

    `bar_stream` is an async iterator yielding appended bars (dict-like with
    ohlcv + timestamp). `is_running()` is a callable polled each bar (ties to
    DB status column for cooperative stop).
    """
    if on_init:
        on_init(ctx)

    async for bar in bar_stream:
        if not is_running():
            log.info("[%s] is_running returned False; exiting", ctx.run_id)
            break
        _append_bar(ctx, bar)
        ctx.current_index = len(ctx._bars_df) - 1
        try:
            on_bar(ctx)
        except Exception:  # noqa: BLE001
            log.exception("[%s] on_bar raised", ctx.run_id)
            continue
        await ctx.flush_orders()


def _append_bar(ctx: LivePaperContext, bar) -> None:
    import pandas as pd
    row = {k: bar.get(k) for k in ("open", "high", "low", "close", "volume")}
    if "timestamp" in bar:
        row["timestamp"] = bar["timestamp"]
    ctx._bars_df = pd.concat([ctx._bars_df, pd.DataFrame([row])], ignore_index=True)
