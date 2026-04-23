"""Dataclass → JSON-safe dict serialization for vendored backTestSys reports.

Handles WFAReport, CPCVReport, numpy arrays, pandas series, datetimes.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime
from typing import Any


def to_json_safe(obj: Any) -> Any:
    """Recursively convert dataclasses / numpy / pandas to JSON-safe primitives."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj):
        return {k: to_json_safe(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_safe(v) for v in obj]
    # numpy / pandas — import lazily
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            return to_json_safe(obj.item())
        if isinstance(obj, np.ndarray):
            return [to_json_safe(v) for v in obj.tolist()]
    except ImportError:
        pass
    try:
        import pandas as pd
        if isinstance(obj, pd.Series):
            return [to_json_safe(v) for v in obj.tolist()]
        if isinstance(obj, pd.DataFrame):
            return [to_json_safe(row) for row in obj.to_dict(orient="records")]
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
    except ImportError:
        pass
    # Fallback: string representation
    return str(obj)


def wfa_report_to_dict(report: Any) -> dict[str, Any]:
    """Flatten a WFAReport into the Phase 2 API shape."""
    d = to_json_safe(report)
    # Add derived fields used by verdict logic + Streamlit
    oos_sharpes = d.get("oos_sharpes", [])
    is_sharpes = d.get("is_sharpes", [])
    d["n_rounds"] = len(d.get("rounds", []))
    d["mean_oos_sharpe"] = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0
    d["mean_is_sharpe"] = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
    # Stitched OOS Sharpe approximation — mean is serviceable; full stitching
    # would require per-round return arrays which may not be in the report
    d["stitched_oos_sharpe"] = d["mean_oos_sharpe"]
    return d


def cpcv_report_to_dict(report: Any) -> dict[str, Any]:
    d = to_json_safe(report)
    d["pct_positive_sharpe"] = report.pct_positive_sharpe if hasattr(report, "pct_positive_sharpe") else d.get("pct_positive_sharpe", 0.0)
    d["verdict_raw"] = report.verdict if hasattr(report, "verdict") else d.get("verdict_raw", "UNKNOWN")
    return d
