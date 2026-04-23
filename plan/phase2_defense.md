# Phase 2 — Defense Component

**Goal:** ship `/api/research/defense` endpoint as a QD backend service. Given a QD strategy + data range, returns a CPCV/WFA/Deflated-Sharpe report with a HEALTHY/OVERFIT/INCONCLUSIVE verdict. UI surfaces the verdict; drill-down happens in an embedded Streamlit dashboard.

**Why this phase first (among research services):** it's strictly additive (doesn't compete with QD's basic backtester), has a well-defined output (a report), and delivers immediate user value ("tell me if my strategy is real or curve-fit").

**Effort:** 1 weekend (~14 hours).
**Wall-clock:** 2 days.

---

## 1. Entry criteria
- Phase 1 complete, exit tests pass
- ASR ScriptStrategy parity test green
- Plugin package loads in backend container

## 2. Exit criteria (hard gates)
1. **Endpoint operational**: `POST /api/research/defense` accepts a valid payload, returns `{job_id, status: "queued"}` in ≤100ms.
2. **Worker processes job**: Redis-queued worker picks up job, completes WFA on 2 years of BTC 15m data in ≤10 min.
3. **Verdict accuracy**: Known-good BTC 15m config (Sharpe 2.556, WFA 1.273) receives verdict `HEALTHY`. Known-overfit synthetic config receives `OVERFIT`.
4. **Results persisted**: report stored in Postgres `bts_defense_reports` table, retrievable via `GET /api/research/defense/{job_id}`.
5. **Streamlit drill-down**: clicking report URL opens Streamlit page showing per-round equity curves, IS/OOS Sharpe bars, DSR p-value, CPCV distribution.
6. **Upstream sync drill still passes**: all Phase 1 + Phase 2 patches survive a synthetic upstream merge.

## 3. Deliverables

### 3.1 New files
```
AxeQuant/
├── backend_api_python/
│   ├── app/
│   │   ├── routes/
│   │   │   └── research.py                     (NEW — /api/research/*)
│   │   ├── services/
│   │   │   └── backtestsys_plugin/
│   │   │       ├── defense/                     (vendored: cpcv, wfa, dsr, reality_check)
│   │   │       ├── orchestrator/                (vendored: runner, fast_runner, parallel)
│   │   │       ├── core/                        (vendored: types, portfolio, utils)
│   │   │       ├── config/                      (vendored: loader)
│   │   │       ├── api/
│   │   │       │   ├── __init__.py
│   │   │       │   ├── defense_service.py       (NEW — orchestration)
│   │   │       │   ├── verdict.py               (NEW — HEALTHY/OVERFIT logic)
│   │   │       │   └── serializer.py            (NEW — dataclass → JSON)
│   │   │       └── workers/
│   │   │           └── defense_worker.py        (NEW — Redis consumer)
│   ├── migrations/
│   │   └── versions/
│   │       └── bts_001_defense_reports.py       (NEW — Postgres migration)
│   └── app/__init__.py                          (PATCHED — register blueprint)
├── streamlit_dashboard/
│   ├── Dockerfile                               (NEW)
│   ├── main.py                                  (NEW — entry)
│   ├── pages/
│   │   └── defense_report.py                    (NEW — drill-down page)
│   └── shared/
│       └── db.py                                (NEW — postgres client)
├── docker-compose.override.yml                   (NEW — adds streamlit service)
└── tests/
    └── research/
        ├── test_defense_endpoint.py
        ├── test_defense_worker.py
        ├── test_verdict_logic.py
        └── fixtures/
            ├── healthy_asr_config.json
            └── overfit_synthetic_config.json
```

### 3.2 Patched upstream files
- `backend_api_python/app/__init__.py` — register `research` blueprint
  - **Diff:** ~2 lines
- No other upstream patches (everything else is additive)

## 4. Implementation steps

### Step 1 — Vendor defense + orchestrator (1 hr)

Extend `scripts/vendor_sync.sh`:
```bash
for mod in signals defense orchestrator core config evaluation/metrics.py evaluation/statistical.py; do
  rsync -av --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$SOURCE/$mod" "$DEST/$mod"
done
```

Re-run sync, close the import graph again (any new `from backTestSys.X` → rewrite).

### Step 2 — Design API contract (45 min)

**Request** `POST /api/research/defense`:
```json
{
  "strategy_id": "strat_abc123",         // QD strategy reference
  "data_range": {
    "symbol": "BTCUSDT:USDT",
    "timeframe": "15m",
    "start": "2023-01-01",
    "end": "2024-12-31"
  },
  "mode": "full",                         // "wfa" | "cpcv" | "full"
  "config": {
    "wfa": {"n_rounds": 6, "is_oos_ratio": 4, "step_mode": "anchored"},
    "cpcv": {"n_splits": 10, "k_test_splits": 2}
  }
}
```

**Response** (immediate):
```json
{ "job_id": "job_def_7xz9", "status": "queued", "polling_url": "/api/research/defense/job_def_7xz9" }
```

**Poll response** (when done):
```json
{
  "job_id": "job_def_7xz9",
  "status": "done",
  "result": {
    "wfa": {
      "stitched_oos_sharpe": 2.556,
      "efficiency": 1.273,
      "n_rounds": 6,
      "rounds": [...]
    },
    "cpcv": { "mean_sharpe": 2.41, "std": 0.28, "n_paths": 45, "distribution": [...] },
    "deflated_sharpe": { "dsr": 0.97, "n_trials": 142, "var_trials": 0.08 },
    "verdict": "HEALTHY",
    "verdict_reason": "OOS Sharpe > 1.0, efficiency > 0.5, DSR > 0.95",
    "report_url": "http://localhost:8501/defense_report?job=job_def_7xz9"
  },
  "completed_at": "2026-04-25T10:15:23Z"
}
```

### Step 3 — Postgres migration (30 min)

`migrations/versions/bts_001_defense_reports.py`:
```python
"""bts_defense_reports table"""
def upgrade():
    op.create_table('bts_defense_reports',
        sa.Column('job_id', sa.String(32), primary_key=True),
        sa.Column('strategy_id', sa.String(64), nullable=False, index=True),
        sa.Column('user_id', sa.Integer, nullable=False, index=True),
        sa.Column('status', sa.String(16), nullable=False),  # queued/running/done/failed
        sa.Column('request', sa.JSON, nullable=False),
        sa.Column('result', sa.JSON, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
    )
```

All our tables prefixed `bts_` to avoid upstream-schema collisions.

### Step 4 — Route + service (3 hr)

`backend_api_python/app/routes/research.py`:
```python
from flask import Blueprint, request, jsonify
from app.services.backtestsys_plugin.api.defense_service import enqueue_defense_job, get_defense_job

research_bp = Blueprint("research", __name__, url_prefix="/api/research")

@research_bp.route("/defense", methods=["POST"])
def submit_defense():
    payload = request.get_json()
    # Validate: strategy_id exists, data_range sane, mode in {wfa, cpcv, full}
    job_id = enqueue_defense_job(payload, user_id=current_user_id())
    return jsonify({"job_id": job_id, "status": "queued", "polling_url": f"/api/research/defense/{job_id}"})

@research_bp.route("/defense/<job_id>", methods=["GET"])
def poll_defense(job_id):
    record = get_defense_job(job_id)
    if not record: return jsonify({"error": "not found"}), 404
    return jsonify(record.to_dict())
```

Register in `app/__init__.py`:
```python
from app.routes.research import research_bp
app.register_blueprint(research_bp)
```

### Step 5 — Defense service orchestrator (3 hr)

`api/defense_service.py`:
```python
def enqueue_defense_job(payload: dict, user_id: int) -> str:
    job_id = gen_job_id()
    record = DefenseReport(job_id=job_id, strategy_id=payload["strategy_id"],
                          user_id=user_id, status="queued", request=payload)
    db.session.add(record); db.session.commit()
    redis_conn.rpush("bts:defense:jobs", job_id)
    return job_id


def process_defense_job(job_id: str) -> None:
    record = DefenseReport.query.get(job_id)
    record.status = "running"; db.session.commit()
    try:
        strategy = load_qd_strategy(record.strategy_id)
        df = load_market_data(**record.request["data_range"])

        cfg = build_bt_config(strategy, df, record.request)
        mode = record.request["mode"]

        result = {}
        if mode in ("wfa", "full"):
            result["wfa"] = run_wfa(cfg, record.request["config"]["wfa"])
        if mode in ("cpcv", "full"):
            result["cpcv"] = run_cpcv(cfg, record.request["config"]["cpcv"])
        if mode == "full":
            result["deflated_sharpe"] = compute_dsr(result["wfa"])

        result["verdict"], result["verdict_reason"] = decide_verdict(result)
        result["report_url"] = build_streamlit_url(job_id)

        record.result = result
        record.status = "done"
    except Exception as e:
        record.error = str(e); record.status = "failed"
    finally:
        record.completed_at = datetime.utcnow()
        db.session.commit()
```

### Step 6 — Verdict logic (45 min)

`api/verdict.py`:
```python
@dataclass
class VerdictGate:
    min_wfa_oos_sharpe: float = 1.0
    min_wfa_efficiency: float = 0.5
    min_dsr: float = 0.95
    min_cpcv_mean_over_std: float = 2.0  # robustness: mean / std >= 2

def decide_verdict(result: dict) -> tuple[str, str]:
    g = VerdictGate()
    failures = []

    if "wfa" in result:
        w = result["wfa"]
        if w["stitched_oos_sharpe"] < g.min_wfa_oos_sharpe:
            failures.append(f"OOS Sharpe {w['stitched_oos_sharpe']:.2f} < {g.min_wfa_oos_sharpe}")
        if w["efficiency"] < g.min_wfa_efficiency:
            failures.append(f"WFA efficiency {w['efficiency']:.2f} < {g.min_wfa_efficiency}")

    if "deflated_sharpe" in result:
        if result["deflated_sharpe"]["dsr"] < g.min_dsr:
            failures.append(f"DSR {result['deflated_sharpe']['dsr']:.2f} < {g.min_dsr}")

    if "cpcv" in result:
        c = result["cpcv"]
        ratio = c["mean_sharpe"] / (c["std"] or 1e-9)
        if ratio < g.min_cpcv_mean_over_std:
            failures.append(f"CPCV stability {ratio:.2f} < {g.min_cpcv_mean_over_std}")

    if not failures:
        return "HEALTHY", "All gates passed"
    if len(failures) >= 2:
        return "OVERFIT", "; ".join(failures)
    return "INCONCLUSIVE", "; ".join(failures)
```

### Step 7 — Worker process (1.5 hr)

`workers/defense_worker.py`:
```python
import time, redis
from app.services.backtestsys_plugin.api.defense_service import process_defense_job

def run():
    r = redis.Redis.from_url(os.environ["REDIS_URL"])
    while True:
        _, job_id = r.blpop("bts:defense:jobs", timeout=30)
        if job_id: process_defense_job(job_id.decode())

if __name__ == "__main__": run()
```

Add worker to `docker-compose.override.yml`:
```yaml
services:
  defense_worker:
    build: ./backend_api_python
    command: python -m app.services.backtestsys_plugin.workers.defense_worker
    depends_on: [postgres, redis]
    environment: [REDIS_URL, DATABASE_URL]
```

### Step 8 — Streamlit dashboard (2 hr)

`streamlit_dashboard/main.py`: landing page, lists recent jobs.
`streamlit_dashboard/pages/defense_report.py`:
```python
import streamlit as st
from shared.db import load_defense_report

job_id = st.query_params.get("job", [""])[0]
report = load_defense_report(job_id)

st.title(f"Defense Report — {report.strategy_id}")
st.metric("Verdict", report.result["verdict"])

col1, col2, col3 = st.columns(3)
col1.metric("OOS Sharpe", f"{report.result['wfa']['stitched_oos_sharpe']:.3f}")
col2.metric("WFA Efficiency", f"{report.result['wfa']['efficiency']:.3f}")
col3.metric("DSR", f"{report.result['deflated_sharpe']['dsr']:.3f}")

st.subheader("Walk-Forward Rounds")
st.bar_chart(pd.DataFrame(report.result["wfa"]["rounds"]).set_index("round_idx")[["is_sharpe", "oos_sharpe"]])

st.subheader("CPCV Distribution")
st.histogram(report.result["cpcv"]["distribution"])
```

### Step 9 — Docker compose override (30 min)
```yaml
services:
  streamlit:
    build: ./streamlit_dashboard
    ports: ["8501:8501"]
    depends_on: [postgres]
  defense_worker:
    # ... as above
```

Bring up:
```bash
docker compose up --build streamlit defense_worker
```

### Step 10 — Test suite (2.5 hr)

Fixtures needed:
- `healthy_asr_config.json` — the known-good BTC 15m ASR (our Sharpe 2.556 champion)
- `overfit_synthetic_config.json` — a strategy curve-fit to pre-2024 that fails 2024

`test_defense_endpoint.py`:
```python
def test_submit_returns_job_id(client):
    resp = client.post("/api/research/defense", json=fixture_payload())
    assert resp.status_code == 200
    assert resp.json["status"] == "queued"

def test_healthy_strategy_verdict(client, worker):
    r = client.post("/api/research/defense", json=healthy_payload())
    job_id = r.json["job_id"]
    wait_for_completion(client, job_id, timeout=600)
    result = client.get(f"/api/research/defense/{job_id}").json
    assert result["result"]["verdict"] == "HEALTHY"

def test_overfit_strategy_verdict(client, worker):
    r = client.post("/api/research/defense", json=overfit_payload())
    job_id = r.json["job_id"]
    wait_for_completion(client, job_id, timeout=600)
    result = client.get(f"/api/research/defense/{job_id}").json
    assert result["result"]["verdict"] == "OVERFIT"
```

### Step 11 — Upstream sync drill + commit (45 min)

Run sync script, ensure no conflicts, commit:
```bash
git add .
git commit -m "Phase 2: /api/research/defense + WFA/CPCV/DSR verdict + Streamlit drill-down"
git push origin main
```

## 5. Test plan

| Test | Purpose |
|---|---|
| `test_submit_returns_job_id` | Endpoint accepts valid payload, enqueues |
| `test_submit_rejects_invalid` | 400 on missing strategy_id / bad date range |
| `test_worker_processes_queue` | Worker consumes Redis queue, updates Postgres |
| `test_healthy_strategy_verdict` | HEALTHY verdict for known-good config |
| `test_overfit_strategy_verdict` | OVERFIT verdict for curve-fit synthetic |
| `test_inconclusive_verdict` | INCONCLUSIVE when 1 gate fails |
| `test_report_url_opens_streamlit` | URL returns 200 from Streamlit service |
| `test_phase1_parity_still_passes` | Phase 1 gates don't regress |

Runtime budget: full suite ≤ 30 min (mostly in WFA computation).

## 6. Rollback plan

1. Drop `research` blueprint registration in `app/__init__.py`
2. `docker compose down streamlit defense_worker`
3. `alembic downgrade -1` (drops `bts_defense_reports`)
4. Plugin `api/`, `workers/`, `defense/`, `orchestrator/` stay — reusable from CLI

Partial rollback is cheap since endpoints are additive.

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| WFA on 2 years of data exceeds 10 min budget | High | Use `fast_runner` + parallel rounds; benchmark early |
| Redis worker crashes mid-job leaves stuck "running" status | Medium | Heartbeat + timeout reaper; dead-letter queue |
| QD strategy → backTestSys config translation is lossy | High | Phase 1 parity test guards signal accuracy; extend it to full config path in Phase 2 Step 4 |
| Postgres JSON column size limits on large rounds arrays | Low | Cap rounds to 20; if needed, spill to object storage |
| Streamlit CORS when iframed from Vue | Low | Configure `CORS_ALLOW_ORIGINS` in nginx |

## 8. Dependencies on later phases

- Phase 3 (autoresearch) reuses the same worker pattern, verdict logic, DB schema idioms.
- Phase 4 (paper trading) consumes the `HEALTHY` verdict as a hard gate before promotion.

## 9. Effort tracking

| Step | Est. | Actual |
|---|---|---|
| 1. Vendor defense + orchestrator | 1 h | |
| 2. API contract | 0.75 h | |
| 3. Postgres migration | 0.5 h | |
| 4. Route + blueprint | 3 h | |
| 5. Service orchestrator | 3 h | |
| 6. Verdict logic | 0.75 h | |
| 7. Worker process | 1.5 h | |
| 8. Streamlit dashboard | 2 h | |
| 9. Docker compose override | 0.5 h | |
| 10. Test suite | 2.5 h | |
| 11. Sync drill + commit | 0.75 h | |
| **Total** | **~16 h** | |
