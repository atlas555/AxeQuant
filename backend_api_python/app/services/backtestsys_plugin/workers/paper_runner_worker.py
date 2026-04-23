"""Paper runner worker — consumes bts:paper:jobs and drives live_paper sessions.

One worker process may handle multiple concurrent runs via asyncio. Each run
is tied to a PaperRun DB record; the runner polls `status` to detect a
cooperative stop.
"""

from __future__ import annotations

import asyncio
import logging
import os

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("bts.paper_runner_worker")


def _get_qd_exchange(name: str, testnet: bool):
    """Delegate to upstream QD live_trading factory."""
    from app.services.live_trading.factory import get_exchange
    return get_exchange(name, testnet=testnet)


async def run_paper_session(run_id: str):
    """Set up and drive a single paper trading session."""
    from app.extensions import db
    from app.services.backtestsys_plugin.api.models import PaperRun
    from app.services.backtestsys_plugin.live.runner import LivePaperContext, run_session
    from app.services.backtestsys_plugin.live.pnl_tracker import record_snapshot
    from app.services.backtestsys_plugin.api.common import utcnow
    from app.services.strategy_script_runtime import compile_strategy_script_handlers

    rec = PaperRun.query.filter_by(id=run_id).first()
    if rec is None:
        log.error("PaperRun %s not found", run_id); return

    exchange = _get_qd_exchange(rec.exchange, testnet=rec.testnet)
    strategy_code = _load_strategy_script(rec.strategy_id)
    on_init, on_bar = compile_strategy_script_handlers(strategy_code)

    import pandas as pd
    warmup_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    # TODO: prewarm with historical bars from QD's kline service

    ctx = LivePaperContext(
        bars_df=warmup_df, initial_balance=rec.initial_capital,
        symbol=(rec.config or {}).get("symbol", "BTC/USDT"),
        exchange=exchange, run_id=run_id,
    )

    rec.status = "running"; rec.started_at = utcnow(); db.session.commit()

    def is_running():
        db.session.refresh(rec)
        return rec.status == "running"

    bar_stream = _stream_bars(exchange,
                              symbol=(rec.config or {}).get("symbol", "BTC/USDT"),
                              timeframe=(rec.config or {}).get("timeframe", "15m"),
                              is_running=is_running)

    try:
        async for _ in _tick_wrapper(ctx, on_init, on_bar, bar_stream, is_running, run_id):
            record_snapshot(run_id, ctx.equity, ctx.position, db_session=db.session)
            db.session.commit()
    except asyncio.CancelledError:
        log.info("Paper run %s cancelled", run_id)
    finally:
        rec.status = "stopped" if rec.status != "failed" else rec.status
        rec.stopped_at = utcnow()
        db.session.commit()


async def _stream_bars(exchange, symbol, timeframe, is_running):
    """Async generator: one bar per completed candle. Uses CCXT watch_ohlcv if available."""
    # CCXT pro (ccxtpro) vs rest polling — sniff at runtime
    if hasattr(exchange, "watch_ohlcv"):
        while is_running():
            try:
                candles = await exchange.watch_ohlcv(symbol, timeframe)
                for c in candles:
                    yield {"timestamp": c[0], "open": c[1], "high": c[2],
                           "low": c[3], "close": c[4], "volume": c[5]}
            except Exception:  # noqa: BLE001
                log.exception("watch_ohlcv failed; backing off")
                await asyncio.sleep(5)
    else:
        # REST polling fallback
        while is_running():
            await asyncio.sleep(15)
            try:
                candles = await _maybe_await(
                    exchange.fetch_ohlcv(symbol, timeframe, limit=2)
                )
                for c in candles[-1:]:
                    yield {"timestamp": c[0], "open": c[1], "high": c[2],
                           "low": c[3], "close": c[4], "volume": c[5]}
            except Exception:  # noqa: BLE001
                log.exception("fetch_ohlcv failed")


async def _maybe_await(obj):
    if asyncio.iscoroutine(obj):
        return await obj
    return obj


async def _tick_wrapper(ctx, on_init, on_bar, bar_stream, is_running, run_id):
    from app.services.backtestsys_plugin.live.runner import run_session
    # Convert the single-call run_session into a generator by inlining the loop.
    # Alternative cleaner path would be to expose per-bar yield from run_session.
    if on_init:
        on_init(ctx)
    async for bar in bar_stream:
        if not is_running():
            break
        import pandas as pd
        ctx._bars_df = pd.concat(
            [ctx._bars_df, pd.DataFrame([bar])], ignore_index=True,
        )
        ctx.current_index = len(ctx._bars_df) - 1
        try:
            on_bar(ctx)
        except Exception:  # noqa: BLE001
            log.exception("[%s] on_bar raised", run_id)
            continue
        await ctx.flush_orders()
        yield ctx


def _load_strategy_script(strategy_id: str) -> str:
    """Load strategy Python source from QD's strategy table.

    Assumes upstream schema has a `qd_strategies.script` column. Adjust if
    upstream renames — keep this the only site that reads strategy source.
    """
    from app.extensions import db
    row = db.session.execute(
        "SELECT script FROM qd_strategies WHERE id = :sid", {"sid": strategy_id},
    ).first()
    if not row or not row[0]:
        raise ValueError(f"strategy {strategy_id} has no script column")
    return row[0]


def main():
    from app import create_app
    from app.services.backtestsys_plugin.api.common import PAPER_QUEUE, get_redis

    app = create_app()

    async def poll_loop():
        loop = asyncio.get_event_loop()
        while True:
            # Blocking pop in a thread so we don't block the event loop
            run_id = await loop.run_in_executor(None, lambda: PAPER_QUEUE.blpop(timeout=30))
            if run_id is None:
                continue
            loop.create_task(run_paper_session(run_id))

    with app.app_context():
        asyncio.run(poll_loop())


if __name__ == "__main__":
    main()
