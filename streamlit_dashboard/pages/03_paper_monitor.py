"""Paper Monitor — per-run equity + drift view."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from shared.db import load_paper_run, load_paper_snapshots

st.set_page_config(page_title="Paper Monitor", layout="wide")

run_id = st.query_params.get("run", "")
if isinstance(run_id, list):
    run_id = run_id[0] if run_id else ""

if not run_id:
    st.warning("Missing ?run=<run_id>")
    st.stop()

run = load_paper_run(run_id)
if run is None:
    st.error(f"Paper run {run_id} not found."); st.stop()

st.title(f"📈 Paper Monitor — {run_id}")

cols = st.columns(4)
cols[0].metric("Status", run["status"])
cols[1].metric("Exchange", f"{run['exchange']} {'(testnet)' if run['testnet'] else '(!LIVE!)'}")
cols[2].metric("Initial Capital", f"${run['initial_capital']:,.0f}")
cols[3].metric("Drift Violations", run.get("drift_violations", 0))

st.divider()

snapshots = load_paper_snapshots(run_id, limit=5000)
if not snapshots:
    st.info("No snapshots yet — runner may still be warming up.")
    st.stop()

snapshots.reverse()  # DB returns newest-first; chart wants oldest-first
df = pd.DataFrame(snapshots)

st.subheader("Equity Curve")
st.line_chart(df.set_index("ts")["equity"])

if "position_size" in df.columns:
    st.subheader("Position Size")
    st.line_chart(df.set_index("ts")["position_size"])

st.divider()
st.subheader("Raw snapshot tail")
st.dataframe(df.tail(50), use_container_width=True)

if run["status"] == "running":
    st.info("Auto-refresh every 30s")
    st.markdown("<meta http-equiv='refresh' content='30'>", unsafe_allow_html=True)
