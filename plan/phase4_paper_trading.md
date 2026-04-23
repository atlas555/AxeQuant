# Phase 4 — Paper Trading Integration

**Goal:** wire a HEALTHY + autoresearch-optimized strategy into QD's upstream `live_trading/` CCXT adapters running on testnet. Validate that live fills reconcile with backtest expectations within 2% PnL drift over 48 hours.

**Why this phase:** this is the first time our research actually touches an exchange API. It's where backtest-vs-live drift reveals itself. Paper mode on testnet is the cheap insurance before any real money.

**Effort:** 1 weekend (~12 hours).
**Wall-clock:** 2 days build + 48 hrs validation.

---

## 1. Entry criteria
- Phase 3 autoresearch working
- At least one HEALTHY + promoted strategy in DB
- Binance testnet (or preferred exchange testnet) API keys available
- QD's upstream `live_trading/` CCXT adapters confirmed unmodified

## 2. Exit criteria (hard gates)
1. **Promote button works**: clicking "Promote to Paper" on a HEALTHY strategy creates a `bts_paper_runs` record, starts CCXT connector on testnet.
2. **Connector opens session**: orders queued, WebSocket live data flowing, heartbeat healthy.
3. **First order executes**: buy/sell signal from strategy results in testnet order_fill event.
4. **48-hour paper run completes**: no crashes, reconnection handled, all intended orders placed.
5. **Drift gate passes**: `abs(live_pnl - backtest_expected_pnl) / backtest_expected_pnl ≤ 0.02` over the 48h window, OR both PnLs < $1 (trivial window).
6. **Emergency stop works**: manually hitting stop button flattens positions and marks run `stopped`.

## 3. Deliverables

### 3.1 New files
```
AxeQuant/
├── backend_api_python/app/services/backtestsys_plugin/
│   ├── api/
│   │   └── paper_service.py                    (NEW — promote/start/stop paper runs)
│   ├── live/                                   (NEW — NOT vendored; orchestration only)
│   │   ├── runner.py                            (bridges strategy → ccxt adapters)
│   │   ├── signal_to_order.py                   (translates strategy output → ccxt order)
│   │   └── pnl_tracker.py                       (tracks live vs backtest-expected)
│   └── workers/
│       └── paper_runner_worker.py               (one worker per active paper run)
├── backend_api_python/migrations/versions/
│   └── bts_003_paper_runs.py
├── streamlit_dashboard/pages/
│   └── paper_monitor.py                         (live PnL + drift dashboard)
└── tests/
    └── paper/
        ├── test_paper_promote.py
        ├── test_signal_to_order.py
        └── test_drift_gate.py
```

### 3.2 Patched upstream files
- None. QD's `live_trading/` is used as a library; we call its `factory.get_exchange(...)` from our runner.

## 4. Implementation steps

### Step 1 — Understand QD live_trading API (1 hr)

Deep read on:
- `live_trading/factory.py` — exchange instantiation
- `live_trading/base.py` — order submission interface
- `live_trading/binance.py` — concrete reference
- `live_trading/execution.py` — the outer orchestration QD uses (may be reusable or we replace)

**Question to resolve:** does QD's `live_trading/execution.py` already do what we need (run a loop, feed bars, call user's on_bar, place orders), or is it too coupled to QD's own strategy model?

Likely answer: partially reusable for bar feeding, but we'll own order lifecycle since our strategy is a ScriptStrategy with `ctx.buy()/sell()` semantics and our own state.

### Step 2 — `paper_service.py` promote flow (2 hr)

```python
def promote_strategy_to_paper(strategy_id: str, user_id: int,
                              autoresearch_candidate_id: int = None,
                              exchange: str = "binance", testnet: bool = True,
                              initial_capital: float = 10000.0) -> str:
    """Start a paper run. Returns paper_run_id."""
    strategy = load_qd_strategy(strategy_id)
    if autoresearch_candidate_id:
        params = load_candidate_params(autoresearch_candidate_id)
        # Check defense: only HEALTHY candidates promotable
        verdict = get_candidate_verdict(autoresearch_candidate_id)
        if verdict != "HEALTHY":
            raise PermissionError(f"Candidate verdict is {verdict}, not HEALTHY")
    else:
        params = strategy.current_params

    paper_run = PaperRun(
        id=gen_run_id(), strategy_id=strategy_id, user_id=user_id,
        params=params, exchange=exchange, testnet=testnet,
        initial_capital=initial_capital, status="starting",
    )
    db.session.add(paper_run); db.session.commit()

    # Enqueue worker task
    redis_conn.rpush("bts:paper:start", paper_run.id)
    return paper_run.id


def stop_paper_run(run_id: str) -> None:
    run = PaperRun.query.get(run_id)
    run.status = "stopping"; db.session.commit()
    redis_conn.publish(f"bts:paper:stop:{run_id}", "stop")
```

### Step 3 — Paper runner worker (4 hr)

`workers/paper_runner_worker.py`:
```python
async def run_paper_session(paper_run_id: str):
    run = PaperRun.query.get(paper_run_id)
    exchange = get_qd_exchange(run.exchange, testnet=run.testnet)
    strategy_code = load_strategy_script(run.strategy_id)
    on_init, on_bar = compile_strategy_script_handlers(strategy_code)

    # Create a stateful context that bridges to QD's live_trading
    ctx = LivePaperContext(
        bars_df=None,      # streamed in
        initial_balance=run.initial_capital,
        exchange=exchange,
        symbol=run.config["symbol"],
        run_id=paper_run_id,
    )

    # Initial fill from historical data for indicator warmup
    ctx.prewarm(bars=1000)

    if on_init: on_init(ctx)

    run.status = "running"; db.session.commit()

    async for bar in exchange.stream_bars(run.config["symbol"], run.config["timeframe"]):
        ctx.append_bar(bar)
        on_bar(ctx)
        # LivePaperContext's ctx.buy/sell already triggered testnet orders
        pnl_tracker.record(run.id, ctx.equity, ctx.position)
        if run.status == "stopping": break
        if drift_gate_violated(run): break

    await ctx.flatten_positions()
    run.status = "stopped"; db.session.commit()
```

`live/runner.py` is where `LivePaperContext` lives — a subclass of `StrategyScriptContext` that:
- Inherits `ctx.signal(...)` from Phase 1
- Overrides `ctx.buy()` to call `exchange.create_order(type=market, side=buy, ...)`
- Overrides `ctx.sell()` to do the same for sell
- Maintains `ctx.position` from exchange WebSocket fills
- Maintains `ctx.balance` from account updates

### Step 4 — Signal to order translation (2 hr)

`live/signal_to_order.py`:
```python
def translate_ctx_action(action: dict, symbol: str, exchange: Exchange) -> dict:
    """ctx.buy(price, amount) args → exchange.create_order kwargs."""
    if action["action"] == "buy":
        return {
            "symbol": symbol,
            "side": "buy",
            "type": "market" if action.get("price") is None else "limit",
            "amount": action["amount"],
            "price": action.get("price"),
            "params": {"reduceOnly": False},
        }
    # ... etc for sell, close
```

Reconcile semantics carefully:
- backTestSys treats `ctx.buy` as a next-bar-open market fill
- Live exchanges fill at best-available liquidity — real slippage
- **Accept this drift as expected**; drift gate allows 2% per 48h window

### Step 5 — PnL tracker + drift gate (1.5 hr)

`live/pnl_tracker.py`:
```python
def record(run_id, live_equity, live_position):
    """Log per-bar snapshot to Postgres."""
    db.session.add(PaperSnapshot(run_id=run_id, ts=now(),
                   equity=live_equity, position_size=live_position.size))
    # Keep it simple — no session commit here, commit every 10 bars

def check_drift(run_id: str) -> tuple[bool, float]:
    """Compare live equity vs what backtest would say for same bars."""
    run = PaperRun.query.get(run_id)
    # Replay strategy on historical bars in the same range as the paper run
    replay_equity = offline_replay(run.strategy_id, run.params,
                                   start=run.started_at, end=now())
    live_equity = get_latest_snapshot(run_id).equity
    expected_pnl = replay_equity - run.initial_capital
    live_pnl = live_equity - run.initial_capital
    if abs(expected_pnl) < 1.0:
        return (False, 0.0)  # trivial window, skip
    drift = abs(live_pnl - expected_pnl) / abs(expected_pnl)
    return (drift > 0.02, drift)
```

Drift check runs every 15 min via scheduled task.

### Step 6 — Postgres migration (30 min)

`bts_003_paper_runs.py`:
```python
op.create_table("bts_paper_runs",
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("strategy_id", sa.String(64)),
    sa.Column("candidate_id", sa.Integer, nullable=True),
    sa.Column("params", sa.JSON),
    sa.Column("exchange", sa.String(32)),
    sa.Column("testnet", sa.Boolean),
    sa.Column("initial_capital", sa.Float),
    sa.Column("status", sa.String(16)),    # starting/running/stopping/stopped/failed
    sa.Column("started_at", sa.DateTime),
    sa.Column("stopped_at", sa.DateTime, nullable=True),
    sa.Column("drift_violations", sa.Integer, server_default="0"),
)
op.create_table("bts_paper_snapshots",
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("run_id", sa.String(32), sa.ForeignKey("bts_paper_runs.id"), index=True),
    sa.Column("ts", sa.DateTime, index=True),
    sa.Column("equity", sa.Float),
    sa.Column("position_size", sa.Float),
    sa.Column("position_side", sa.String(8)),
)
```

### Step 7 — Routes (30 min)

Extend `routes/research.py`:
```python
@research_bp.route("/paper/promote", methods=["POST"])
def promote_to_paper():
    payload = request.get_json()  # {strategy_id, candidate_id, exchange, ...}
    run_id = promote_strategy_to_paper(**payload, user_id=current_user_id())
    return jsonify({"run_id": run_id})

@research_bp.route("/paper/<run_id>", methods=["GET"])
def get_paper_run(run_id): ...

@research_bp.route("/paper/<run_id>/stop", methods=["POST"])
def stop_paper(run_id): ...
```

### Step 8 — Streamlit monitor (2 hr)

`streamlit_dashboard/pages/paper_monitor.py`:
- Top: current equity + live position
- Middle: live equity curve vs backtest-expected overlay
- Bottom: drift ticker (refreshing every 15 min)
- Red banner if drift > 2%
- "Emergency Stop" button → calls stop endpoint

### Step 9 — Test suite (1.5 hr)

```python
def test_promote_requires_healthy_verdict(client):
    r = client.post("/api/research/paper/promote",
                    json={"strategy_id": "s1", "candidate_id": overfit_candidate.id})
    assert r.status_code == 403

def test_signal_to_order_mapping():
    action = {"action": "buy", "price": 50000, "amount": 0.1}
    order = translate_ctx_action(action, "BTC/USDT", mock_exchange)
    assert order["side"] == "buy" and order["type"] == "limit"

def test_drift_gate_triggers():
    # Inject a synthetic divergence, assert drift_gate_violated returns True
    ...
```

### Step 10 — 48-hour validation run (wall-clock 2 days)

Pick one HEALTHY strategy (BTC 15m ASR champion), promote to Binance testnet, let run 48h. Exit criteria:
- No unhandled errors
- Drift stays ≤ 2% throughout
- At least 5 orders placed (for meaningful PnL sample)
- Emergency stop tested mid-run

### Step 11 — Sync drill + commit (30 min)

## 5. Test plan summary

| Test | Purpose |
|---|---|
| Only HEALTHY candidates promotable | Enforce defense gate |
| Signal-to-order translation correct | No side/amount swaps |
| Drift gate math correct | Triggers at >2%, not before |
| Emergency stop flattens positions | No orphan positions |
| Reconnection handles exchange drops | Resilience |
| 48h live validation | Real test — no substitute |

## 6. Rollback plan

1. `POST /api/research/paper/{run_id}/stop` on all active runs
2. Drop endpoints, migrations, disable workers
3. Strategy stays promoted but becomes inert without workers

Partial rollback keeps research (Phase 2-3) intact.

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| QD `live_trading/execution.py` tightly coupled to QD strategy model | High | Skip it — write our own bar loop in `live/runner.py`; use CCXT directly via `factory.get_exchange()` |
| Testnet order semantics differ from mainnet | Medium | Document known quirks (min order size, cooldowns, different fee tiers) |
| WebSocket disconnections during 48h run | High | Auto-reconnect with backoff; persist seq numbers; resume from last snapshot |
| Offline replay for drift check is expensive | Medium | Run every 15 min not every bar; cache bar data |
| Live fills have slippage — 2% drift is tight for low-volume 15m BTC | Medium | Reconsider threshold after first run; 5% may be realistic |
| Paper context's `ctx.signal()` uses stale bars on tick edges | Medium | Lock signal cache to completed bars only |

## 8. Dependencies on later phases

Phase 5 consumes paper runs as the qualification window for real-money deployment.

## 9. Effort tracking

| Step | Est. | Actual |
|---|---|---|
| 1. Study QD live_trading | 1 h | |
| 2. paper_service promote flow | 2 h | |
| 3. Paper runner worker | 4 h | |
| 4. Signal-to-order | 2 h | |
| 5. PnL tracker + drift | 1.5 h | |
| 6. Postgres migration | 0.5 h | |
| 7. Routes | 0.5 h | |
| 8. Streamlit monitor | 2 h | |
| 9. Test suite | 1.5 h | |
| 10. 48h validation | (wall-clock) | |
| 11. Sync + commit | 0.5 h | |
| **Total engineering** | **~15.5 h** | |
| **Validation** | **48 h wall-clock** | |
