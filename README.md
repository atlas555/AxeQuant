<div align="center">

  <h1>AxeQuant</h1>
  <h3>Research-Grade Validation on Top of QuantDinger</h3>
  <p><strong>Fork of <a href="https://github.com/brokermr810/QuantDinger">QuantDinger</a> that ships backTestSys into the product shell — CPCV, Walk-Forward, Deflated Sharpe, autoresearch, and a hard kill-switch before real money.</strong></p>

  <p>
    <a href="https://github.com/brokermr810/QuantDinger"><strong>Upstream</strong></a> &nbsp;·&nbsp;
    <a href="plan/README.md"><strong>Plan</strong></a> &nbsp;·&nbsp;
    <a href="UPSTREAM_PATCHES.md"><strong>Patch Registry</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=flat-square" alt="License"></a>
    <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/Fork%20Policy-Pull%20Only-lightgrey?style=flat-square" alt="Fork Policy">
    <img src="https://img.shields.io/badge/Phase-1%20%E2%9C%93-green?style=flat-square" alt="Phase 1 done">
  </p>

</div>

---

## TL;DR

AxeQuant = **QuantDinger's product shell** + **backTestSys's research depth**.

- QuantDinger gives you the Vue UI, Flask API, strategy authoring (IndicatorStrategy JSON, ScriptStrategy Python, AI codegen), and CCXT-based live execution across 9+ exchanges.
- backTestSys contributes CPCV + Walk-Forward Analysis + Deflated Sharpe Ratio + structural autoresearch — the defense layer that distinguishes a real edge from a curve-fit number.
- This fork pulls from upstream. **It never pushes back.** All additions live in `backtestsys_plugin/` or behind isolated patches registered in [`UPSTREAM_PATCHES.md`](UPSTREAM_PATCHES.md).

## Why this fork exists

A private backtest that reports Sharpe 2.5 means one of two things:
1. A genuine edge you can trade.
2. An overfit artifact that will give it all back — often in the first live month.

Telling them apart is what this fork adds. Every strategy authored in QD is one click away from a HEALTHY / OVERFIT / INCONCLUSIVE verdict backed by CPCV + WFA + DSR. Only HEALTHY candidates become promotable to paper, and only after 14 days of clean paper do they become promotable to live.

## What's new versus upstream

| Area | Upstream | AxeQuant addition |
|---|---|---|
| Strategy authoring | IndicatorStrategy JSON, ScriptStrategy Python, AI codegen | `ctx.signal(name, **params)` exposes 16 research-grade signals (asrband, wavetrend, order-flow, regime, confluence) inside ScriptStrategy |
| Backtest | Single-split historical test | CPCV + Walk-Forward + Deflated Sharpe report per strategy (Phase 2) |
| Parameter search | Manual / AI-assisted | Autoresearch loop with OOS objective + defense gate on every candidate (Phase 3) |
| Paper trading | Manual via `live_trading/` | Promote-from-verdict flow with 48h parity guard (Phase 4) |
| Live trading | Direct go-live | 14-day qualification window + hard kill-switch on Sharpe drift > 40% (Phase 5) |

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Foundation: fork + vendor signals + ASR ScriptStrategy + parity gate | **done** (commit `ca6ecb1`) |
| 2 | Defense component: `/api/research/defense` + WFA/CPCV/DSR verdict | designed |
| 3 | Autoresearch: `/api/research/autoresearch` + param-space optimizer | designed |
| 4 | Paper trading: promote → CCXT testnet + drift guard | designed |
| 5 | Live trading gate: 14d qualification + kill-switch | designed |

Full per-phase design: [`plan/`](plan/).

## Quick start (run upstream as-is)

The upstream bring-up still works unchanged:

```bash
git clone https://github.com/atlas555/AxeQuant.git && cd AxeQuant
cp backend_api_python/env.example backend_api_python/.env
./scripts/generate-secret-key.sh
docker-compose up -d --build
```

Open `http://localhost:8888`, log in with `quantdinger` / `123456`.

## Using the plugin (Phase 1)

Inside any QD ScriptStrategy you can now call:

```python
def on_bar(ctx):
    asr = ctx.signal("asrband", asr_length=94, band_mult=0.22,
                     ewm_halflife=178, channel_width=6.5, cooldown_bars=8)
    if asr.long1 or asr.long2:
        ctx.buy(price=ctx.bars(1)[0].close, amount=...)
    elif asr.all_long_sl and ctx.position.get("side") == "long":
        ctx.close_position()
```

Full example: [`docs/examples/asr_script_strategy.py`](docs/examples/asr_script_strategy.py).

Available signals (Phase 1):
```
absorption, asrband, atr, confluence_filter, cvd, cvd_divergence,
ema, mfi, regime_detector, rsi, sma, true_delta, volume_regime,
volume_threshold, vwap_distance, wavetrend
```

Signal math runs in vendored plugin code, **not** inside QD's `safe_exec` sandbox. `ctx.signal` is bound at context construction before the user script runs.

## Development

```bash
# Vendor/refresh signals from backTestSys
bash scripts/vendor_sync.sh

# Run plugin test suite (no backend deps required)
python -m pytest tests/backtestsys_plugin/ -v

# Sync with upstream QuantDinger
git fetch upstream
git merge upstream/main
# Then verify every patch in UPSTREAM_PATCHES.md still applies
python -m pytest tests/backtestsys_plugin/
```

## Fork policy

- **Pull only.** `git remote` lists `origin` (this fork) and `upstream` (brokermr810/QuantDinger). No PRs go upstream.
- **Additive changes preferred.** New files land in `backtestsys_plugin/`, `tests/backtestsys_plugin/`, `plan/`, `streamlit_dashboard/`.
- **Patches minimized.** Edits to upstream-owned files must be registered in [`UPSTREAM_PATCHES.md`](UPSTREAM_PATCHES.md) with commit sha, line range, reason, and removal instructions.

## Acknowledgements

- [QuantDinger](https://github.com/brokermr810/QuantDinger) — the product shell this fork extends. Apache 2.0.
- backTestSys (internal) — the research layer being vendored in.

## License

Apache 2.0, inherited from upstream. See [`LICENSE`](LICENSE).
