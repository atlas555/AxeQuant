# Phase 5 — Live Trading Gate

**Goal:** deploy a proven strategy to **real money** with hard kill-switch guards. Only promotable after 2 consecutive weeks of paper-trading parity on testnet. Live performance continuously compared to OOS expectation; auto-flatten on drift.

**Why this phase:** this is where code meets capital. Everything prior is recoverable; live losses are not. The engineering work is small but the gating and monitoring discipline is severe.

**Effort:** 0.5 weekend (~6 hours engineering) + 2 weeks paper validation + ongoing monitoring.
**Wall-clock:** 2 weeks minimum before first live trade.

---

## 1. Entry criteria
- Phase 4 paper trading working end-to-end
- At least one strategy with 2+ weeks of clean paper run data (no crashes, drift < 2% sustained)
- Real-money exchange account with appropriate margin + risk limits
- Kill-switch tested in Phase 4

## 2. Exit criteria (hard gates — strictly more conservative than Phase 4)

1. **Qualification window**: strategy must have ≥14 days continuous paper run with:
   - Zero unexpected errors
   - Live Sharpe (realized on paper) within ±30% of backtest OOS Sharpe 2.556
   - Max drawdown on paper ≤ 1.5x backtest max drawdown
   - At least 30 trades (for sample size)
2. **Manual approval required**: `promote_to_live` requires explicit confirmation step, cannot be triggered by autoresearch or any automated path.
3. **Capital cap enforced**: initial live deployment capital ≤ $1000 (or user-configured `LIVE_MAX_CAPITAL` env var, whichever is lower).
4. **Kill-switch active**: drift > 40% OR drawdown > 2x backtest max → auto-flatten + disable strategy.
5. **Observability**: separate Streamlit dashboard just for live monitoring; paged notifications (Telegram/Feishu) on kill-switch fire.
6. **Audit log**: every order, every position change, every PnL reconciliation persisted immutably.

## 3. Deliverables

### 3.1 New files
```
AxeQuant/
├── backend_api_python/app/services/backtestsys_plugin/
│   ├── api/
│   │   └── live_service.py                     (NEW — promote_to_live, requires manual confirm)
│   ├── live/
│   │   ├── kill_switch.py                      (NEW — monitoring + auto-flatten)
│   │   ├── audit_log.py                        (NEW — immutable order log)
│   │   └── qualification.py                    (NEW — paper→live gate check)
│   └── workers/
│       └── live_monitor_worker.py              (NEW — per-live-strategy monitor)
├── backend_api_python/migrations/versions/
│   ├── bts_004_live_runs.py
│   └── bts_005_audit_log.py
├── streamlit_dashboard/pages/
│   └── live_monitor.py
├── backend_api_python/app/services/backtestsys_plugin/notifications/
│   ├── telegram_alert.py
│   └── feishu_alert.py
└── tests/
    └── live/
        ├── test_qualification_gate.py
        ├── test_kill_switch.py
        └── test_audit_log_immutable.py
```

### 3.2 Patched upstream files
- Environment variable additions in `env.example`: `LIVE_MAX_CAPITAL`, `LIVE_TELEGRAM_BOT`, `LIVE_FEISHU_WEBHOOK`
  - **Diff:** ~4 lines appended

## 4. Implementation steps

### Step 1 — Qualification check (1.5 hr)

`live/qualification.py`:
```python
@dataclass
class QualificationResult:
    qualified: bool
    reasons: list[str]
    paper_sharpe: float
    backtest_oos_sharpe: float
    sharpe_drift_pct: float
    max_dd_ratio: float
    n_trades: int

def check_qualification(paper_run_id: str, min_days: int = 14,
                        max_sharpe_drift_pct: float = 30.0,
                        max_dd_multiplier: float = 1.5,
                        min_trades: int = 30) -> QualificationResult:
    run = PaperRun.query.get(paper_run_id)
    age_days = (now() - run.started_at).days

    reasons = []
    if age_days < min_days:
        reasons.append(f"Paper age {age_days}d < required {min_days}d")
    if run.status != "running":
        reasons.append(f"Paper status is {run.status}, not 'running'")

    snapshots = PaperSnapshot.query.filter_by(run_id=paper_run_id).order_by("ts").all()
    paper_sharpe = compute_sharpe(snapshots)
    backtest_oos = load_strategy_backtest_oos_sharpe(run.strategy_id, run.params)
    drift = abs(paper_sharpe - backtest_oos) / backtest_oos * 100
    if drift > max_sharpe_drift_pct:
        reasons.append(f"Sharpe drift {drift:.1f}% > {max_sharpe_drift_pct}%")

    paper_max_dd = compute_max_dd(snapshots)
    backtest_max_dd = load_strategy_backtest_max_dd(run.strategy_id, run.params)
    if paper_max_dd > backtest_max_dd * max_dd_multiplier:
        reasons.append(f"Paper DD {paper_max_dd:.2%} > {max_dd_multiplier}x backtest {backtest_max_dd:.2%}")

    n_trades = count_paper_trades(paper_run_id)
    if n_trades < min_trades:
        reasons.append(f"Only {n_trades} trades, need ≥{min_trades}")

    return QualificationResult(
        qualified=not reasons, reasons=reasons,
        paper_sharpe=paper_sharpe, backtest_oos_sharpe=backtest_oos,
        sharpe_drift_pct=drift, max_dd_ratio=paper_max_dd / backtest_max_dd,
        n_trades=n_trades,
    )
```

### Step 2 — Live service promote flow (1 hr)

`api/live_service.py`:
```python
def promote_to_live(paper_run_id: str, user_id: int, capital: float,
                    confirmation_token: str) -> str:
    """Promote a qualifying paper run to live.

    Args:
        confirmation_token: MUST be a short-lived token generated from
                            /api/research/live/request-confirmation endpoint.
                            Prevents automation from calling this directly.
    """
    verify_confirmation_token(confirmation_token, user_id)
    qual = check_qualification(paper_run_id)
    if not qual.qualified:
        raise PermissionError(f"Not qualified: {qual.reasons}")

    max_cap = float(os.environ.get("LIVE_MAX_CAPITAL", 1000.0))
    if capital > max_cap:
        raise ValueError(f"Capital {capital} > max allowed {max_cap}")

    paper = PaperRun.query.get(paper_run_id)
    live_run = LiveRun(
        id=gen_run_id(), paper_run_id=paper_run_id,
        strategy_id=paper.strategy_id, params=paper.params,
        exchange=paper.exchange, testnet=False,
        capital=capital, status="starting",
        user_id=user_id, qualification=qual.to_dict(),
    )
    db.session.add(live_run); db.session.commit()
    redis_conn.rpush("bts:live:start", live_run.id)
    send_alert(user_id, f"LIVE STARTED: {live_run.id} with ${capital}")
    return live_run.id
```

Confirmation flow:
1. UI calls `POST /api/research/live/request-confirmation` → returns token valid for 5 min
2. UI shows "are you sure" modal with qualification summary
3. User clicks confirm → UI calls `POST /api/research/live/promote` with token + capital

Prevents accidental promotion and defeats any automated pipeline that might try to skip human review.

### Step 3 — Kill switch (1.5 hr)

`live/kill_switch.py`:
```python
@dataclass
class KillSwitchConfig:
    max_drift_pct: float = 40.0
    max_dd_multiplier: float = 2.0
    check_interval_seconds: int = 300  # 5 min
    consecutive_breaches_required: int = 2  # avoid single-tick false alarms


class KillSwitchMonitor:
    def __init__(self, live_run_id: str, cfg: KillSwitchConfig):
        self.run_id = live_run_id
        self.cfg = cfg
        self.breach_count = 0

    def check(self) -> bool:
        """Returns True if kill switch should fire."""
        breach = False
        drift = compute_live_sharpe_drift(self.run_id)
        if drift > self.cfg.max_drift_pct: breach = True
        dd_ratio = compute_live_dd_ratio(self.run_id)
        if dd_ratio > self.cfg.max_dd_multiplier: breach = True

        if breach:
            self.breach_count += 1
        else:
            self.breach_count = 0

        return self.breach_count >= self.cfg.consecutive_breaches_required

    def fire(self):
        """Flatten all positions, disable strategy, alert user."""
        run = LiveRun.query.get(self.run_id)
        run.status = "killed"; run.killed_at = now()
        run.kill_reason = "drift/dd threshold breach"
        db.session.commit()

        exchange = get_qd_exchange(run.exchange, testnet=False)
        positions = exchange.fetch_positions([run.config["symbol"]])
        for p in positions:
            exchange.create_market_order(
                run.config["symbol"],
                "sell" if p["side"] == "long" else "buy",
                p["contracts"],
            )
        send_urgent_alert(run.user_id,
            f"🚨 KILL SWITCH FIRED for {run.id}\n"
            f"Positions flattened. Reason: {run.kill_reason}")
```

### Step 4 — Audit log (1 hr)

`live/audit_log.py`:
```python
def log_event(run_id: str, event_type: str, payload: dict) -> None:
    """Immutable append-only log. Never update, never delete."""
    row = AuditLog(
        id=uuid(), run_id=run_id, event_type=event_type,
        payload=payload, ts=now(),
        hash=compute_hash(run_id, event_type, payload),
    )
    db.session.add(row); db.session.commit()
```

Postgres table uses `CREATE TABLE ... WITH (fillfactor=100)` and trigger to reject UPDATE/DELETE at DB level. Event types: `order_submitted`, `order_filled`, `order_rejected`, `position_opened`, `position_closed`, `drift_check`, `kill_switch_fired`, `user_action`.

### Step 5 — Live monitor worker (45 min)

`workers/live_monitor_worker.py`:
```python
async def monitor_live_run(run_id: str):
    run = LiveRun.query.get(run_id)
    ks = KillSwitchMonitor(run_id, KillSwitchConfig())

    while run.status == "running":
        await asyncio.sleep(ks.cfg.check_interval_seconds)
        if ks.check():
            ks.fire()
            break
        db.session.refresh(run)
```

Runs one asyncio task per active live run. Separate process from the strategy runner (which is effectively the Phase 4 paper runner extended).

### Step 6 — Notifications (1 hr)

`notifications/telegram_alert.py` — simple webhook POST.
`notifications/feishu_alert.py` — similar.

Both read from env vars. Fail silently if not configured, but log warning.

### Step 7 — Live monitor page (1 hr)

`streamlit_dashboard/pages/live_monitor.py`:
- Banner: "LIVE — REAL MONEY" (red background, unmissable)
- Current P&L (live-updated)
- Position size + notional exposure
- Drift ticker vs backtest
- Last 20 orders (linked to audit log)
- Kill switch status (armed/fired)
- Manual kill button (separate from emergency stop — for when kill switch logic itself is suspect)

### Step 8 — Tests (1.5 hr)

```python
def test_qualification_rejects_short_paper():
    result = check_qualification(short_run.id, min_days=14)
    assert not result.qualified
    assert "Paper age" in result.reasons[0]

def test_qualification_rejects_high_drift():
    # Synthetic run with 50% sharpe drift
    result = check_qualification(drifty_run.id)
    assert not result.qualified

def test_promote_to_live_requires_confirmation_token():
    with pytest.raises(InvalidTokenError):
        promote_to_live(qualified_run.id, user_id=1, capital=500,
                       confirmation_token="fake")

def test_kill_switch_fires_on_sustained_breach():
    ks = KillSwitchMonitor("test_run", KillSwitchConfig(consecutive_breaches_required=2))
    inject_breach()
    assert not ks.check()  # 1st breach
    assert ks.check()      # 2nd breach fires

def test_kill_switch_resets_on_recovery():
    # breach then recover then breach — should not fire
    ...

def test_audit_log_rejects_update():
    log_event("r1", "order_submitted", {...})
    with pytest.raises(IntegrityError):
        db.session.execute("UPDATE bts_audit_log SET payload='{}' WHERE run_id='r1'")
```

### Step 9 — 2-week paper qualification window (wall-clock)

Before any live deployment:
1. Select target strategy
2. Start paper run on testnet
3. Monitor daily via Streamlit
4. Weekly review: Sharpe drift, DD, trade count, connector reliability
5. At day 14, run `check_qualification` — if pass, proceed to Step 10
6. If fail, diagnose, iterate on strategy, restart clock from day 0

### Step 10 — Live deployment (30 min active)

```bash
# User workflow
1. Open Streamlit live monitor
2. Click "Request Live Promotion" → get confirmation token (valid 5 min)
3. Review qualification modal: drift %, DD ratio, trade count
4. Enter capital amount ≤ $1000
5. Click "PROMOTE TO LIVE — CONFIRM"
6. Monitor tightly for first 24 hours
```

### Step 11 — Sync drill + commit (30 min)

## 5. Test plan summary

| Test | Purpose |
|---|---|
| Qualification gate rejects unqualified runs | No unsafe promotion |
| Confirmation token required | No accidental/automated promotion |
| Capital cap enforced | Blast radius limited |
| Kill switch fires on drift | Fail-safe works |
| Kill switch fires on excess DD | Fail-safe works |
| Kill switch does NOT fire on noise | No false positives |
| Audit log is immutable | Forensic trust |
| Alerts delivered | Human-in-the-loop |

## 6. Rollback plan

If live trading goes wrong:
1. Hit kill switch manually → flatten all positions
2. Set `status = "emergency_stop"` on all live runs
3. Disable `/api/research/live/promote` route (env flag `DISABLE_LIVE=1`)
4. Postmortem via audit log
5. Return to paper-only while root-causing

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Kill switch itself has a bug | **Catastrophic** | (1) consecutive_breaches_required=2 prevents flapping; (2) manual kill button; (3) exchange-side position limits; (4) small capital cap |
| Exchange outage during kill attempt | High | Retry with exponential backoff; alert user within 60s if can't reach exchange |
| Drift-check metrics are lagging indicators | Medium | Supplement with position-level risk checks (notional exposure, leverage) |
| Confirmation token phished or leaked | Low | Short TTL (5 min); single-use; IP-bound |
| User overrides capital cap in env | User error | Cap is `min(user_input, env_LIVE_MAX_CAPITAL)` — code ignores user if env is stricter |
| Tax/accounting not captured | Medium (non-trading) | Audit log + separate quarterly export pipeline (out of scope here) |
| Strategy edge decays between paper and live | Expected | Re-run defense quarterly on new data; re-qualify before capital increases |

## 8. Ongoing operations (post-Phase-5)

| Cadence | Action |
|---|---|
| Continuous | Live monitor streams to Streamlit + Telegram |
| Hourly | Drift check, DD check |
| Daily | Email summary: PnL, trades, notable events |
| Weekly | Kill switch config review |
| Monthly | Re-run Phase 2 defense on updated data |
| Quarterly | Re-run Phase 3 autoresearch, potential re-qualification |

## 9. Effort tracking

| Step | Est. | Actual |
|---|---|---|
| 1. Qualification check | 1.5 h | |
| 2. Live service promote | 1 h | |
| 3. Kill switch | 1.5 h | |
| 4. Audit log | 1 h | |
| 5. Live monitor worker | 0.75 h | |
| 6. Notifications | 1 h | |
| 7. Live monitor page | 1 h | |
| 8. Tests | 1.5 h | |
| 9. 2-week qualification | (wall-clock) | |
| 10. Live deployment | 0.5 h | |
| 11. Sync + commit | 0.5 h | |
| **Total engineering** | **~10 h** | |
| **Qualification wait** | **14 days** | |

## 10. The most important instruction

**Do not shorten the 14-day qualification window.** Every hour of temptation to skip comes back as hours of pain post-loss. Backtest Sharpe 2.556 on BTC 15m is a claim, not a fact. Two weeks of clean paper is the minimum evidence that the claim survives contact with reality. Hold the line.
