# Phase 3 — Autoresearch Service

**Goal:** ship `/api/research/autoresearch` endpoint. Given a QD strategy + a param-space definition + data range, runs backTestSys's structural optimizer (autoresearch loop) and returns ranked param sets with OOS metrics.

**Why this phase:** most strategies are authored at default params. Autoresearch is where backTestSys's real edge shows up — we went from Sharpe 0.50 to 2.556 on BTC 15m via the same framework.

**Effort:** 2 weekends (~26 hours).
**Wall-clock:** 4 days.

---

## 1. Entry criteria
- Phase 2 defense service operational
- ASR ScriptStrategy runs cleanly in QD
- Postgres `bts_*` prefix convention established

## 2. Exit criteria (hard gates)
1. **Endpoint operational**: `POST /api/research/autoresearch` accepts payload with param space, returns job_id.
2. **Optimizer runs**: worker executes autoresearch loop on ASR BTC 15m, produces ≥5 candidate param sets within 30 min.
3. **Top result beats baseline**: top-ranked OOS Sharpe > baseline OOS Sharpe by ≥10%, or explicit "no improvement found" status.
4. **Each candidate has defense report**: every returned candidate also has a `defense_job_id` linking to a Phase 2 report (reuse, don't duplicate).
5. **Streamlit UI**: param-space exploration page shows candidate ranking, filterable by verdict, linked to defense drill-down.
6. **Upstream sync drill passes**.

## 3. Deliverables

### 3.1 New files
```
AxeQuant/
├── backend_api_python/app/services/backtestsys_plugin/
│   ├── optimizer/                              (vendored: param_spec, optimizer, report)
│   ├── api/
│   │   ├── autoresearch_service.py             (NEW)
│   │   └── param_space.py                      (NEW — schema validator)
│   └── workers/
│       └── autoresearch_worker.py              (NEW)
├── backend_api_python/migrations/versions/
│   └── bts_002_autoresearch_reports.py         (NEW)
├── streamlit_dashboard/pages/
│   └── autoresearch_explorer.py                (NEW)
└── tests/research/
    ├── test_autoresearch_endpoint.py
    ├── test_param_space.py
    └── fixtures/
        └── asr_param_space.json
```

### 3.2 No new upstream patches
Phase 3 is fully additive (just new endpoints, new plugin code).

## 4. Implementation steps

### Step 1 — Vendor optimizer (45 min)
Add to `vendor_sync.sh`:
```bash
rsync -av --delete "$SOURCE/optimizer/" "$DEST/optimizer/"
```
Audit imports, close graph.

### Step 2 — Param space schema (2.5 hr)

`api/param_space.py`:
```python
"""ParamSpace — declarative definition of what optimizer should search.

Example JSON:
{
  "asr_length": {"type": "int", "range": [50, 150], "step": 2},
  "band_mult": {"type": "float", "range": [0.1, 0.5], "step": 0.01},
  "ewm_halflife": {"type": "int", "range": [50, 300], "step": 10},
  "enable_short3": {"type": "bool"},
  "tp_fracs.long1": {"type": "choice", "values": [0.5, 0.75, 1.0]}
}
"""
from dataclasses import dataclass
from typing import Any, Literal

ParamType = Literal["int", "float", "bool", "choice"]

@dataclass
class ParamDef:
    name: str
    type: ParamType
    range: list | None = None       # [min, max] for int/float
    step: float | None = None
    values: list | None = None      # for choice

def parse_param_space(payload: dict) -> list[ParamDef]:
    """Validate + convert incoming JSON to ParamDef list."""
    defs = []
    for name, spec in payload.items():
        t = spec.get("type")
        if t in ("int", "float"):
            assert "range" in spec and len(spec["range"]) == 2
            defs.append(ParamDef(name, t, range=spec["range"], step=spec.get("step")))
        elif t == "bool":
            defs.append(ParamDef(name, t))
        elif t == "choice":
            assert "values" in spec
            defs.append(ParamDef(name, t, values=spec["values"]))
        else:
            raise ValueError(f"Unknown param type: {t}")
    return defs

def size_estimate(defs: list[ParamDef]) -> int:
    """Rough combinatorial size — used to gate oversized searches."""
    size = 1
    for d in defs:
        if d.type == "int" and d.step:
            size *= max(1, int((d.range[1] - d.range[0]) / d.step))
        elif d.type == "float" and d.step:
            size *= max(1, int((d.range[1] - d.range[0]) / d.step))
        elif d.type == "bool":
            size *= 2
        elif d.type == "choice":
            size *= len(d.values)
    return size
```

### Step 3 — API contract (30 min)

**Request** `POST /api/research/autoresearch`:
```json
{
  "strategy_id": "strat_abc123",
  "data_range": {...},
  "param_space": { ... see above ... },
  "objective": "oos_sharpe",           // or "oos_calmar", "oos_sortino"
  "budget": {
    "max_iterations": 100,
    "max_wall_seconds": 1800,
    "early_stop_patience": 20
  },
  "defense_on_each_candidate": true   // run Phase 2 defense on top-K
}
```

**Response**:
```json
{ "job_id": "job_ar_xyz", "estimated_space_size": 4500, "status": "queued" }
```

**Poll response**:
```json
{
  "status": "done",
  "result": {
    "n_iterations": 87,
    "candidates": [
      {
        "rank": 1,
        "params": {...},
        "oos_sharpe": 2.61,
        "oos_calmar": 1.89,
        "n_trades": 234,
        "defense_job_id": "job_def_abc",
        "verdict": "HEALTHY"
      },
      ...
    ],
    "baseline": {"oos_sharpe": 2.35, "params": {...}},
    "improvement_pct": 11.1,
    "stopped_reason": "early_stop_patience_exceeded"
  }
}
```

### Step 4 — Postgres migration (30 min)

`bts_002_autoresearch_reports.py`:
```python
op.create_table("bts_autoresearch_reports",
    sa.Column("job_id", sa.String(32), primary_key=True),
    sa.Column("strategy_id", sa.String(64), index=True),
    sa.Column("user_id", sa.Integer, index=True),
    sa.Column("status", sa.String(16)),
    sa.Column("request", sa.JSON),
    sa.Column("result", sa.JSON),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("completed_at", sa.DateTime, nullable=True),
)
op.create_table("bts_autoresearch_candidates",
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("job_id", sa.String(32), sa.ForeignKey("bts_autoresearch_reports.job_id")),
    sa.Column("rank", sa.Integer),
    sa.Column("params", sa.JSON),
    sa.Column("oos_sharpe", sa.Float, index=True),
    sa.Column("n_trades", sa.Integer),
    sa.Column("defense_job_id", sa.String(32), nullable=True),
    sa.Column("verdict", sa.String(16), nullable=True),
)
```

### Step 5 — Autoresearch service (5 hr)

`api/autoresearch_service.py`:
```python
def enqueue_autoresearch_job(payload: dict, user_id: int) -> str:
    defs = parse_param_space(payload["param_space"])
    size = size_estimate(defs)
    if size > 100_000:
        raise ValueError(f"Param space too large: {size} — tighten ranges")
    job_id = gen_job_id()
    # persist record
    redis_conn.rpush("bts:autoresearch:jobs", job_id)
    return job_id


def process_autoresearch_job(job_id: str) -> None:
    record = AutoresearchReport.query.get(job_id)
    try:
        strategy = load_qd_strategy(record.strategy_id)
        df = load_market_data(**record.request["data_range"])
        param_defs = parse_param_space(record.request["param_space"])
        budget = record.request["budget"]

        # Baseline: current strategy params
        baseline_metrics = evaluate(strategy, df, strategy.current_params)

        # Autoresearch loop (imported from vendored optimizer/)
        from app.services.backtestsys_plugin.optimizer import AutoresearchLoop
        loop = AutoresearchLoop(
            strategy=strategy, data=df, param_space=param_defs,
            objective=record.request["objective"],
            max_iterations=budget["max_iterations"],
            max_wall_seconds=budget["max_wall_seconds"],
            early_stop_patience=budget["early_stop_patience"],
        )
        candidates = loop.run()  # returns sorted list of (params, metrics)

        # Optionally run Phase 2 defense on top-K
        top_k = min(5, len(candidates))
        if record.request.get("defense_on_each_candidate"):
            from app.services.backtestsys_plugin.api.defense_service import enqueue_defense_job
            for c in candidates[:top_k]:
                c["defense_job_id"] = enqueue_defense_job_sync(
                    record.strategy_id, c["params"], record.request["data_range"])

        record.result = {
            "n_iterations": loop.n_iterations,
            "candidates": candidates,
            "baseline": baseline_metrics,
            "improvement_pct": (candidates[0]["oos_sharpe"] - baseline_metrics["oos_sharpe"])
                              / baseline_metrics["oos_sharpe"] * 100,
            "stopped_reason": loop.stopped_reason,
        }
        record.status = "done"
    except Exception as e:
        record.error = str(e); record.status = "failed"
    finally:
        db.session.commit()
```

### Step 6 — Worker (1 hr)
Mirror defense worker pattern. Long-running jobs; configure separate worker pool with higher CPU limits.

### Step 7 — Route registration (30 min)
Extend `routes/research.py`:
```python
@research_bp.route("/autoresearch", methods=["POST"])
def submit_autoresearch(): ...

@research_bp.route("/autoresearch/<job_id>", methods=["GET"])
def poll_autoresearch(job_id): ...

@research_bp.route("/autoresearch/<job_id>/candidate/<rank>/promote", methods=["POST"])
def promote_candidate(job_id, rank):
    """Write candidate params back onto the strategy as a new version."""
    ...
```

### Step 8 — Streamlit explorer (3 hr)

`streamlit_dashboard/pages/autoresearch_explorer.py`:
- Input: `?job=job_ar_xyz`
- Show param-space scatter (Sharpe vs Calmar, colored by verdict)
- Table of top 20 candidates, sortable, filter by HEALTHY only
- Click candidate → drill-down with param diff vs baseline, defense report link
- "Promote" button calls promote_candidate endpoint

### Step 9 — Test suite (4 hr)

Fixtures: `asr_param_space.json` (realistic space for BTC 15m ASR), `asr_baseline_strategy.json`.

```python
def test_param_space_validation():
    with pytest.raises(ValueError):
        parse_param_space({"bad": {"type": "unknown"}})

def test_size_estimate_rejects_huge_space(client):
    huge = {f"p{i}": {"type": "int", "range": [0, 100], "step": 1} for i in range(10)}
    r = client.post("/api/research/autoresearch", json={...param_space: huge})
    assert r.status_code == 400

def test_autoresearch_improves_baseline(client, worker):
    r = client.post("/api/research/autoresearch", json=asr_payload())
    job_id = r.json["job_id"]
    wait_for_completion(client, job_id, timeout=1800)
    result = client.get(f"/api/research/autoresearch/{job_id}").json["result"]
    assert result["improvement_pct"] >= 0  # may not improve but shouldn't regress
    assert len(result["candidates"]) >= 5

def test_candidates_have_defense_reports(client, worker):
    ...
    for c in result["candidates"][:5]:
        assert c["defense_job_id"] is not None
        assert c["verdict"] in ("HEALTHY", "OVERFIT", "INCONCLUSIVE")
```

### Step 10 — Sync drill + commit (45 min)

## 5. Test plan summary

| Test | Purpose |
|---|---|
| Param-space schema validation | Reject malformed spaces |
| Size estimate caps oversized searches | Prevent runaway jobs |
| Baseline beats or matches current strategy | Sanity check |
| Defense reports generated for top candidates | Integration with Phase 2 |
| Promote candidate creates new strategy version | Workflow continuity |
| Early stop triggers on no improvement | Budget compliance |

## 6. Rollback plan

Same pattern as Phase 2. Drop endpoints, downgrade migrations, leave plugin code for CLI use.

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Autoresearch loop doesn't converge in 30 min on realistic space | High | Warm-start from best-so-far; enable distributed parallel workers |
| Per-candidate defense runs balloon total time | High | Only run defense on top-5; option flag to skip |
| Non-determinism in optimizer causes test flakes | Medium | Seed random state in tests; compare to tolerance not exact |
| Param space too coupled to strategy internals | High | Document that param_space keys must match strategy's `ctx.param(...)` names; validate at submission |
| Autoresearch produces curve-fit winners (looks great, isn't) | Very high | **Defense verdict is the promotion gate, not raw Sharpe.** Only HEALTHY candidates are promotable. |

## 8. Dependencies on later phases

- Phase 4 (paper trading) uses promoted candidates as the live trading entry point.

## 9. Effort tracking

| Step | Est. | Actual |
|---|---|---|
| 1. Vendor optimizer | 0.75 h | |
| 2. Param space schema | 2.5 h | |
| 3. API contract | 0.5 h | |
| 4. Postgres migration | 0.5 h | |
| 5. Autoresearch service | 5 h | |
| 6. Worker | 1 h | |
| 7. Routes | 0.5 h | |
| 8. Streamlit explorer | 3 h | |
| 9. Test suite | 4 h | |
| 10. Sync + commit | 0.75 h | |
| **Total** | **~18.5 h** | |

Reality buffer: push to ~26 h given optimizer convergence tuning is typically painful.
