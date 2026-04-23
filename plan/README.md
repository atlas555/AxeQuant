# AxeQuant — Plan Directory

Fork of [brokermr810/QuantDinger](https://github.com/brokermr810/QuantDinger).
Goal: ship **backTestSys research power** (CPCV/WFA/DSR + autoresearch + structural optimizer) into **QuantDinger's end-to-end product shell** (Vue UI + Flask API + CCXT live execution).

**Integration direction:** QD is the product. backTestSys is a capability pack vendored in as a plugin package + targeted upstream patches.

**Fork policy:** pull from upstream only. Never push. All additions live in new files/dirs where possible; edits to upstream files are minimal and registered in `UPSTREAM_PATCHES.md`.

---

## Phase Map

| Phase | Goal | Effort | Wall-clock | Entry criteria |
|---|---|---|---|---|
| [1 — Foundation](phase1_foundation.md) | Fork, vendor signals, ASR ScriptStrategy runs identically to backTestSys | 1 weekend | 2 days | Upstream cloned |
| [2 — Defense Component](phase2_defense.md) | `/api/research/defense` endpoint returns WFA/CPCV/DSR + verdict | 1 weekend | 2 days | Phase 1 done |
| [3 — Autoresearch](phase3_autoresearch.md) | `/api/research/autoresearch` endpoint runs structural optimizer | 2 weekends | 4 days | Phase 2 done |
| [4 — Paper Trading](phase4_paper_trading.md) | Promoted configs trade on testnet via QD live_trading/ CCXT adapters | 1 weekend | 2 days | Phase 3 done |
| [5 — Live Trading Gate](phase5_live_trading.md) | Real money with hard Sharpe-drift kill switch | 0.5 weekend + 2 weeks validation | 2 weeks | Phase 4 stable |

**Total engineering:** ~5.5 weekends. **Total wall-clock to live money:** ~4 weeks.

---

## Critical path

```
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
 (fork+   (defense    (optimizer    (paper on    (live on
 signals)  service)    service)     testnet)    mainnet)
```

Nothing is parallelizable. Each phase has a concrete exit test; don't advance without passing.

---

## Per-phase exit tests (summary)

- **Phase 1**: ASR strategy backtested via QD ScriptStrategy and via raw `backTestSys.runner` on same data produces identical equity curve (diff ≤ 1e-6 per bar).
- **Phase 2**: `/api/research/defense` called on a known-good BTC 15m config returns `wfa_oos_sharpe ≥ 1.0` and verdict `HEALTHY` within 10 min.
- **Phase 3**: `/api/research/autoresearch` produces ≥5 ranked param sets, top result has higher OOS Sharpe than the input baseline.
- **Phase 4**: Paper-traded 48h on testnet; live fills reconcile with backtest expectation within 2% PnL drift.
- **Phase 5**: 2 weeks of paper (or shadow live) with live Sharpe within ±30% of OOS Sharpe 2.556; kill-switch triggers correctly on synthetic drift injection.

---

## Repo layout after Phase 1

```
AxeQuant/                                  (forked from upstream)
├── backend_api_python/
│   ├── app/services/backtestsys_plugin/   (NEW — vendored + adapters)
│   └── app/services/strategy_script_runtime.py  (PATCHED)
├── plan/                                  (THIS DIR)
├── UPSTREAM_PATCHES.md                    (NEW — patch registry)
└── ...                                    (upstream unchanged)
```

Further phases add `routes/research.py`, `streamlit_dashboard/`, Postgres migrations, etc.

---

## Status

| Phase | Status |
|---|---|
| 1 | Designed, ready to implement |
| 2 | Designed |
| 3 | Designed |
| 4 | Designed |
| 5 | Designed |

Implementation begins with Phase 1 immediately after this plan directory is finalized.
