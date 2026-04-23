"""Live trading execution module (used by both paper and live phases).

Contents:
- runner.py: main bar-by-bar loop bridging ScriptStrategy + CCXT exchange
- signal_to_order.py: translate ctx.buy/sell semantics → exchange orders
- pnl_tracker.py: per-bar equity snapshots + drift-vs-backtest computation
- kill_switch.py: drift + drawdown monitoring (Phase 5)
- qualification.py: paper-to-live qualification gate (Phase 5)
- audit_log.py: immutable event log for live compliance (Phase 5)
"""
