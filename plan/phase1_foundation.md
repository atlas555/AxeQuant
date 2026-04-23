# Phase 1 — Foundation

**Goal:** establish the fork topology, vendor backTestSys signals into QD as a plugin package, patch `StrategyScriptContext` to expose signals via `ctx.signal(...)`, and author an ASR ScriptStrategy that produces identical backtest output whether run inside QD's script runtime or via raw backTestSys runner.

**Why this phase first:** every later phase depends on (a) the fork being real and syncable, (b) strategies being expressible as QD ScriptStrategies using our signal math, (c) a proven zero-drift invariant between research and production code paths.

**Effort:** 1 weekend (~12 hours focused).
**Wall-clock:** 2 days.

---

## 1. Entry criteria

- [x] `/Users/allen/coding/github/QuantDinger/` cloned (done 2026-04-22)
- [ ] GitHub account ready to host fork (user action: fork via github.com UI)
- [ ] Docker desktop installed and running
- [ ] Python 3.11+ local env

## 2. Exit criteria (hard gates)

1. **Fork bootstrapped**: `AxeQuant` repo exists on GitHub with upstream remote configured; `git fetch upstream && git merge upstream/main` works.
2. **Plugin package imports cleanly** in QD's Flask runtime: `from app.services.backtestsys_plugin.signals import asrband` succeeds inside backend container.
3. **Patch registry populated**: `UPSTREAM_PATCHES.md` lists every touched upstream file with line-range + reason.
4. **ASR ScriptStrategy parity test passes**: the same ASR strategy run through QD's `compile_strategy_script_handlers` and through `backTestSys.orchestrator.runner.run` on BTC 15m data 2024-01-01→2024-06-30 produces equity curves with **max per-bar diff ≤ 1e-6**.
5. **Upstream sync drill succeeds**: synthetic upstream change merged into fork, our patches re-applied, exit test #4 still passes.

Any gate failing → do not advance to Phase 2.

## 3. Deliverables

### 3.1 Git topology
- `AxeQuant` repo on GitHub (public or private — user choice)
- `upstream` remote → `brokermr810/QuantDinger`
- `origin` remote → user's fork
- Branch protection on `main` (optional but recommended)

### 3.2 New files
```
AxeQuant/
├── UPSTREAM_PATCHES.md
├── plan/                                         (from Phase 0 planning)
├── backend_api_python/
│   ├── app/services/backtestsys_plugin/
│   │   ├── __init__.py
│   │   ├── VERSION                              (sync marker vs source backTestSys)
│   │   ├── signals/
│   │   │   ├── __init__.py
│   │   │   ├── base.py                          (vendored verbatim)
│   │   │   ├── registry.py                      (vendored verbatim)
│   │   │   └── technical/                       (vendored verbatim)
│   │   │       ├── asrband.py
│   │   │       ├── wavetrend.py
│   │   │       ├── atr.py
│   │   │       └── ...
│   │   └── adapters/
│   │       ├── __init__.py
│   │       └── ctx_signals.py                   (NEW — attach_signals helper)
│   └── requirements-backtestsys.txt             (extra deps for plugin)
├── tests/
│   └── backtestsys_plugin/
│       ├── __init__.py
│       ├── test_import.py
│       ├── test_ctx_signals.py
│       └── test_asr_script_parity.py
└── scripts/
    ├── vendor_sync.sh                           (rsync backTestSys → plugin)
    └── upstream_sync.sh                         (git fetch + merge + re-patch)
```

### 3.3 Patched upstream files
- `backend_api_python/app/services/strategy_script_runtime.py`
  - Add import: `from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals`
  - Call `attach_signals(self)` at end of `StrategyScriptContext.__init__`
  - **Diff size:** ~3 lines added, 0 removed

## 4. Implementation steps

### Step 1 — Fork & clone (15 min, user action)
```bash
# On GitHub: fork brokermr810/QuantDinger → allen/AxeQuant
# Then locally:
cd /Users/allen/coding/github/
git clone git@github.com:allen/AxeQuant.git
cd AxeQuant
git remote add upstream https://github.com/brokermr810/QuantDinger.git
git fetch upstream
git branch --set-upstream-to=origin/main main
```

### Step 2 — Move plan/ into fork (5 min)
```bash
mv /Users/allen/coding/github/AxeQuant/plan  AxeQuant/plan  # fix path clash
# Actually: the planning AxeQuant/ and the forked AxeQuant/ must be the same dir.
# If different, copy plan/ into the fork and delete the pre-fork scaffold.
```

**Note:** the pre-fork `/Users/allen/coding/github/AxeQuant/plan/` directory created during planning will be folded into the actual fork clone. Plan is to `git mv` or `cp -r` after clone, then commit.

### Step 3 — Vendor signals via rsync script (1 hr)

Write `scripts/vendor_sync.sh`:
```bash
#!/usr/bin/env bash
# Sync backTestSys signals into plugin. One-way, backTestSys is the source of truth.
set -euo pipefail
SOURCE=/Users/allen/coding/github/AxeAlgo1M/backTestSys
DEST=backend_api_python/app/services/backtestsys_plugin

rsync -av --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$SOURCE/signals/" "$DEST/signals/"

# Phase 1 only vendors signals/. Later phases add defense/, optimizer/, etc.
echo "$(cd "$SOURCE" && git rev-parse HEAD)" > "$DEST/VERSION"
echo "Sync complete. Source: $SOURCE @ $(cat "$DEST/VERSION")"
```

Run it:
```bash
bash scripts/vendor_sync.sh
```

### Step 4 — Rewrite imports in vendored signals (1 hr)

Vendored code imports like `from backTestSys.core.types import Bar` — these won't resolve inside QD. Two options:

**Option A (chosen):** leave imports pointing to `backTestSys.*` and add `backTestSys` as a **sibling vendored package** later if needed. For Phase 1, the ASR ScriptStrategy only needs a small number of signals; we can stub the imports they need.

Actually that's messy. **Better: rewrite imports at vendor-sync time** via sed:

Extend `vendor_sync.sh`:
```bash
# Rewrite imports: backTestSys.X → app.services.backtestsys_plugin.X
find "$DEST" -name '*.py' -exec sed -i.bak \
  -e 's|from backTestSys\.|from app.services.backtestsys_plugin.|g' \
  -e 's|import backTestSys\.|import app.services.backtestsys_plugin.|g' {} \;
find "$DEST" -name '*.bak' -delete
```

This assumes the plugin's internal module graph is self-contained. For Phase 1 we only need `signals/` + its dependency surface. If any signal imports from `backTestSys.core` or `backTestSys.config`, we must also vendor those — document in `VENDOR_DEPS.md`.

**Audit step:** after Phase 1's initial sync, run `grep -rn "from backTestSys" backend_api_python/app/services/backtestsys_plugin/` — result must be empty.

### Step 5 — Vendor minimum dependency surface (2 hr)

Inspect signal imports. Expected minimum:
- `signals/` itself
- `core/types.py` (for `Bar`)
- `core/utils.py` (helpers)
- `config/loader.py` (may be needed for type hints — check)

Run in backTestSys dir:
```bash
grep -rn "^from backTestSys\|^import backTestSys" signals/ | \
  awk -F: '{print $NF}' | sort -u
```

Everything listed gets added to the vendor sync include list. Iterate until import graph is closed.

### Step 6 — Write `ctx_signals.py` adapter (1.5 hr)

```python
# backend_api_python/app/services/backtestsys_plugin/adapters/ctx_signals.py
"""Expose backTestSys signals to QD's StrategyScriptContext as ctx.signal(...)."""
from __future__ import annotations
from typing import Any
from app.services.backtestsys_plugin.signals.registry import get_signal_class


class _SignalProxy:
    """Memoized per-bar accessor. Computes signal columns on first call per (name, params)."""
    def __init__(self, ctx):
        self._ctx = ctx
        self._cache: dict[tuple, Any] = {}

    def __call__(self, name: str, **params):
        key = (name, tuple(sorted(params.items())))
        if key not in self._cache:
            cls = get_signal_class(name)
            signal_obj = cls(**params)
            # Compute on full df — backTestSys signals are vectorized
            self._cache[key] = signal_obj.compute(self._ctx._bars_df)
        # Return current-bar snapshot
        row = self._cache[key].iloc[self._ctx.current_index]
        return _RowView(row)


class _RowView:
    """Attribute-style access to a pandas Series row."""
    def __init__(self, row): self._row = row
    def __getattr__(self, name):
        try: return self._row[name]
        except KeyError: raise AttributeError(name)


def attach_signals(ctx) -> None:
    ctx.signal = _SignalProxy(ctx)
```

### Step 7 — Patch `strategy_script_runtime.py` (15 min)

Locate the `StrategyScriptContext.__init__` and append:
```python
from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals
# ... (after existing init logic) ...
attach_signals(self)
```

Record the patch in `UPSTREAM_PATCHES.md`:
```markdown
## strategy_script_runtime.py
- **Upstream sha at patch time:** <commit-sha>
- **Lines added:** after line ~126 (end of __init__), insert 2 lines
- **Reason:** expose backTestSys signals via ctx.signal(name, **params)
- **Removal:** delete attach_signals call + import
```

### Step 8 — Write ASR ScriptStrategy example (1 hr)

`docs/examples/asr_script_strategy.py`:
```python
# ASR Strategy — QD ScriptStrategy form
# Uses backTestSys asrband signal via ctx.signal()

def on_init(ctx):
    ctx.param("asr_length", 94)
    ctx.param("channel_width", 6.5)
    ctx.param("band_mult", 0.22)
    ctx.param("ewm_halflife", 178)
    ctx.param("risk_per_trade", 0.01)
    ctx.param("rr", 1.5)

def on_bar(ctx):
    asr = ctx.signal("asrband",
        asr_length=ctx.param("asr_length"),
        channel_width=ctx.param("channel_width"),
        band_mult=ctx.param("band_mult"),
        ewm_halflife=ctx.param("ewm_halflife"))

    bar = ctx.bars(1)[0]

    if ctx.position.size == 0:
        if asr.long1_entry:
            size = ctx.balance * ctx.param("risk_per_trade") / asr.stop_distance
            ctx.buy(price=bar.close, amount=size)
        elif asr.short1_entry:
            size = ctx.balance * ctx.param("risk_per_trade") / asr.stop_distance
            ctx.sell(price=bar.close, amount=size)
    else:
        if ctx.position.side == "long" and asr.exit_long:
            ctx.close_position()
        elif ctx.position.side == "short" and asr.exit_short:
            ctx.close_position()
```

### Step 9 — Write parity test (2 hr)

`tests/backtestsys_plugin/test_asr_script_parity.py`:
```python
"""Run same ASR strategy via QD script runtime and via raw backTestSys runner.
Assert equity curves match to 1e-6 per bar."""
import numpy as np
import pandas as pd
import pytest

from app.services.strategy_script_runtime import StrategyScriptContext, compile_strategy_script_handlers
# Also import raw backTestSys runner (from vendored internal path or sibling)

ASR_SCRIPT = open("docs/examples/asr_script_strategy.py").read()

@pytest.fixture
def btc_15m_slice():
    # Load 2024-01-01 → 2024-06-30 BTC 15m from test fixture
    return pd.read_csv("tests/fixtures/btc_15m_2024_h1.csv")

def test_parity(btc_15m_slice):
    # Path A: run via QD script runtime
    on_init, on_bar = compile_strategy_script_handlers(ASR_SCRIPT)
    ctx = StrategyScriptContext(btc_15m_slice, initial_balance=10000.0)
    if on_init: on_init(ctx)
    for i in range(len(btc_15m_slice)):
        ctx.current_index = i
        on_bar(ctx)
    qd_equity = ctx.equity_curve  # assumes QD tracks this

    # Path B: run same logic via raw backTestSys
    from backTestSys_external import run_asr_baseline  # ref runner
    bts_equity = run_asr_baseline(btc_15m_slice,
        asr_length=94, band_mult=0.22, risk_per_trade=0.01)

    max_diff = np.max(np.abs(np.array(qd_equity) - np.array(bts_equity)))
    assert max_diff < 1e-6, f"Drift detected: max_diff={max_diff}"
```

**Note:** QD's `StrategyScriptContext` doesn't yet track `equity_curve` per bar — may need a second small upstream patch to expose it, OR reconstruct it from the orders list. Decide during Step 9.

### Step 10 — Dry-run upstream sync drill (30 min)

```bash
# Simulate upstream releasing a new version
git fetch upstream
git merge upstream/main  # should be no-op if up to date

# Test: edit a line in upstream-owned file manually, commit as if upstream changed it,
# then verify our patches still apply.
```

### Step 11 — Commit + push to fork (15 min)
```bash
git add .
git commit -m "Phase 1: vendor backTestSys signals as plugin, patch ScriptContext"
git push origin main
```

## 5. Test plan

| Test | File | Purpose |
|---|---|---|
| Import smoke | `test_import.py` | Plugin package imports without error |
| ctx.signal registers | `test_ctx_signals.py` | `attach_signals(ctx)` adds `ctx.signal` method |
| ASR signal values match | `test_ctx_signals.py::test_asr_values` | Calling `ctx.signal("asrband")` returns same values as direct `asrband.compute(df)` |
| Script runtime parity | `test_asr_script_parity.py` | Full end-to-end: QD script runtime ↔ backTestSys runner, diff ≤ 1e-6 |
| Upstream merge | `scripts/upstream_sync.sh --dry-run` | Merges clean after synthetic upstream change |

All tests run via `pytest tests/backtestsys_plugin/` inside the backend container.

## 6. Rollback plan

If Phase 1 fails irrecoverably:
1. `git checkout main && git reset --hard upstream/main` — drops all our patches
2. `rm -rf backend_api_python/app/services/backtestsys_plugin` — drops vendored code
3. Return to using backTestSys standalone via `backtest.result/` workflow; abandon QD integration

Reasons to rollback: sandbox breach (our patches compromise safe_exec), upstream merge conflicts exceed ~1hr per sync, parity test diff > 0.01 (indicates fundamental drift we can't eliminate).

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| backTestSys signals depend on big chunks of `core/` / `config/` → vendor surface balloons | High | Start with just asrband; audit import graph before expanding |
| QD's safe_exec rejects our `ctx.signal` method | Medium | `attach_signals` binds BEFORE user script runs; ctx methods aren't subject to sandbox |
| Parity test diff 1e-6 is too tight (pandas/numpy non-determinism) | Medium | Relax to 1e-4; investigate if higher |
| User hasn't forked yet when we start step 2 | Low | Step 1 is explicit user action; pause until confirmed |
| QD trajectory between `ctx.buy()` → equity reconstruction differs from our accounting | High | Step 9 may expand scope; budget +2hr |

## 8. Dependencies on later phases

None. Phase 1 stands alone and its exit test doesn't require any later-phase components.

## 9. Effort tracking

| Step | Est. | Actual |
|---|---|---|
| 1. Fork & clone | 0.25 h | |
| 2. Move plan/ | 0.1 h | |
| 3. vendor_sync.sh | 1 h | |
| 4. Rewrite imports | 1 h | |
| 5. Close import graph | 2 h | |
| 6. ctx_signals.py | 1.5 h | |
| 7. Patch runtime | 0.25 h | |
| 8. ASR example | 1 h | |
| 9. Parity test | 2 h | |
| 10. Sync drill | 0.5 h | |
| 11. Commit | 0.25 h | |
| **Total** | **~10 h** | |

(Buffer to 12 h for debugging.)
