"""Live Monitor — REAL MONEY dashboard with unmissable warning banner."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from shared.db import load_audit_log, load_live_run

st.set_page_config(page_title="LIVE Monitor — AxeQuant", layout="wide")

run_id = st.query_params.get("run", "")
if isinstance(run_id, list):
    run_id = run_id[0] if run_id else ""

st.markdown(
    "<div style='background:#d00;color:white;padding:10px;"
    "text-align:center;font-weight:700;font-size:20px;border-radius:4px;'>"
    "🚨 LIVE — REAL MONEY 🚨"
    "</div>",
    unsafe_allow_html=True,
)

if not run_id:
    st.warning("Missing ?run=<run_id>"); st.stop()

run = load_live_run(run_id)
if run is None:
    st.error(f"Live run {run_id} not found"); st.stop()

st.title(f"Live Run — {run_id}")

cols = st.columns(4)
cols[0].metric("Status", run["status"])
cols[1].metric("Exchange", run["exchange"])
cols[2].metric("Capital", f"${run['capital']:,.2f}")
cols[3].metric("Kill reason", run.get("kill_reason") or "—")

if run.get("qualification"):
    with st.expander("Qualification snapshot"):
        st.json(run["qualification"])

st.divider()
st.subheader("Audit Log (last 200 events)")
events = load_audit_log(run_id, limit=200)
if events:
    df = pd.DataFrame(events)
    cols_show = [c for c in ["ts", "event_type", "payload", "hash"] if c in df.columns]
    st.dataframe(df[cols_show], use_container_width=True)
else:
    st.info("No audit events yet.")

st.divider()
if run["status"] in ("running", "starting"):
    if st.button("🛑 MANUAL KILL", type="primary"):
        import os
        import requests
        base = os.environ.get("BACKEND_URL", "http://backend:5000")
        try:
            r = requests.post(f"{base}/api/research/live/{run_id}/kill", timeout=10)
            st.success(f"kill dispatched: {r.status_code}")
        except Exception as e:  # noqa: BLE001
            st.error(f"kill request failed: {e}")
    st.markdown("<meta http-equiv='refresh' content='30'>", unsafe_allow_html=True)
