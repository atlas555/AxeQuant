"""Defense Report — drill-down for a single WFA/CPCV/DSR job."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from shared.db import load_defense_report

st.set_page_config(page_title="Defense Report", layout="wide")

job_id = st.query_params.get("job", "")
if isinstance(job_id, list):
    job_id = job_id[0] if job_id else ""

if not job_id:
    st.warning("Missing ?job=<job_id> query parameter.")
    st.stop()

report = load_defense_report(job_id)
if report is None:
    st.error(f"Defense report {job_id} not found.")
    st.stop()

st.title(f"🛡️ Defense Report — {job_id}")

status_color = {"done": "green", "running": "orange", "queued": "blue", "failed": "red"}.get(
    report["status"], "gray"
)
st.markdown(f"**Status:** :{status_color}[{report['status']}]")

if report["status"] != "done":
    st.info("Report not yet complete. Refresh to poll.")
    if report.get("error"):
        st.error(report["error"])
    st.stop()

result = report["result"] or {}

verdict = result.get("verdict", "UNKNOWN")
verdict_colors = {"HEALTHY": "green", "OVERFIT": "red", "INCONCLUSIVE": "orange"}
st.markdown(
    f"## Verdict: :{verdict_colors.get(verdict, 'gray')}[**{verdict}**]"
)
st.caption(result.get("verdict_reason", ""))

# Headline metrics
cols = st.columns(4)
wfa = result.get("wfa", {})
cpcv = result.get("cpcv", {})
dsr = result.get("deflated_sharpe") or {}
cols[0].metric("OOS Sharpe (WFA)", f"{wfa.get('stitched_oos_sharpe', 0):.3f}")
cols[1].metric("WFA Efficiency", f"{wfa.get('efficiency', 0):.3f}")
cols[2].metric("Deflated Sharpe", f"{dsr.get('dsr', 0):.3f}" if dsr else "—")
cols[3].metric("CPCV pct positive", f"{cpcv.get('pct_positive_sharpe', 0):.0%}" if cpcv else "—")

st.divider()

# WFA rounds
if wfa and wfa.get("rounds"):
    st.subheader("Walk-Forward Rounds")
    rounds_df = pd.DataFrame(wfa["rounds"])
    keep = [c for c in ["round_idx", "is_sharpe", "oos_sharpe"] if c in rounds_df.columns]
    if keep:
        st.bar_chart(rounds_df.set_index(keep[0])[[c for c in keep if c != keep[0]]])
    st.dataframe(rounds_df, use_container_width=True)

# CPCV distribution
if cpcv and cpcv.get("oos_sharpes"):
    st.subheader("CPCV Out-of-Sample Sharpe Distribution")
    sharpes = pd.DataFrame({"oos_sharpe": cpcv["oos_sharpes"]})
    st.bar_chart(sharpes)
    c1, c2, c3 = st.columns(3)
    c1.metric("Mean", f"{cpcv.get('mean_oos_sharpe', 0):.3f}")
    c2.metric("Std", f"{cpcv.get('std_oos_sharpe', 0):.3f}")
    c3.metric("Paths", cpcv.get("n_paths", 0))

st.divider()
with st.expander("Raw request / result JSON"):
    st.json({"request": report["request"], "result": result})
