"""Autoresearch Explorer — ranked candidate view for a single optimizer job."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from shared.db import load_autoresearch_report

st.set_page_config(page_title="Autoresearch Explorer", layout="wide")

job_id = st.query_params.get("job", "")
if isinstance(job_id, list):
    job_id = job_id[0] if job_id else ""

if not job_id:
    st.warning("Missing ?job=<job_id> query parameter.")
    st.stop()

report = load_autoresearch_report(job_id)
if report is None:
    st.error(f"Autoresearch report {job_id} not found.")
    st.stop()

st.title(f"🔭 Autoresearch Explorer — {job_id}")
status = report["status"]
st.markdown(f"**Status:** `{status}`")
if status != "done":
    if report.get("error"):
        st.error(report["error"])
    st.stop()

result = report["result"] or {}

cols = st.columns(3)
cols[0].metric("Iterations", result.get("n_iterations", 0))
cols[1].metric("Baseline score", f"{result.get('baseline_score', 0):.3f}")
cols[2].metric("Improvement", f"{result.get('improvement_pct', 0):+.1f}%")

st.caption(f"Stopped: {result.get('stopped_reason', 'unknown')}")
st.divider()

candidates = report.get("candidates") or result.get("candidates") or []

st.subheader("Candidates")
if candidates:
    rows = []
    for c in candidates:
        row = {
            "rank": c.get("rank"),
            "oos_sharpe": c.get("oos_sharpe") or c.get("metric"),
            "verdict": c.get("verdict") or "—",
            "n_trades": c.get("n_trades"),
            "defense_job_id": c.get("defense_job_id") or "—",
        }
        # Flatten params for display
        for k, v in (c.get("params") or {}).items():
            row[f"p:{k}"] = v
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("rank")
    st.dataframe(df, use_container_width=True)

    st.subheader("Score distribution")
    st.bar_chart(df.set_index("rank")["oos_sharpe"])
else:
    st.info("No candidates returned.")

with st.expander("Raw request / result JSON"):
    st.json({"request": report["request"], "result": result})
