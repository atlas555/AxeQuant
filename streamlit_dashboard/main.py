"""AxeQuant research dashboard — Streamlit app.

Entry point for the Streamlit container defined in docker-compose.override.yml.
Reads from Postgres (same DB as Flask backend) via shared/db.py.

Pages (lazy-loaded from pages/):
- defense_report.py       — WFA/CPCV/DSR drill-down
- autoresearch_explorer.py — candidate ranking + param-space scatter
- paper_monitor.py         — live paper-trade PnL + drift
- live_monitor.py          — LIVE MONEY monitor with kill-switch controls
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="AxeQuant Research",
    page_icon="🔬",
    layout="wide",
)

st.title("AxeQuant Research Dashboard")

st.markdown("""
This dashboard surfaces outputs from the `backtestsys_plugin` research layer
integrated into QuantDinger.

**Pages:**
- 🛡️  Defense Report — WFA / CPCV / Deflated Sharpe drill-down
- 🔭 Autoresearch Explorer — ranked optimizer candidates
- 📈 Paper Monitor — testnet paper-trade PnL + drift gate
- 🚨 Live Monitor — **real money** dashboard with kill-switch

Pages take a `?job=<id>` or `?run=<id>` query parameter; links are emitted by
the Flask backend when jobs/runs complete.
""")

st.info("Select a page from the sidebar. Direct links open the relevant report.")
