"""Shared Postgres client for Streamlit pages.

Uses the same DATABASE_URL as the Flask backend. Read-only connection pool.
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg2
import psycopg2.extras


def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set — Streamlit container is misconfigured")
    return psycopg2.connect(url)


def fetch_one(query: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def load_defense_report(job_id: str) -> dict | None:
    return fetch_one("SELECT * FROM bts_defense_reports WHERE job_id = %s", (job_id,))


def load_autoresearch_report(job_id: str) -> dict | None:
    report = fetch_one("SELECT * FROM bts_autoresearch_reports WHERE job_id = %s", (job_id,))
    if report is None:
        return None
    candidates = fetch_all(
        "SELECT * FROM bts_autoresearch_candidates WHERE job_id = %s ORDER BY rank",
        (job_id,),
    )
    report["candidates"] = candidates
    return report


def load_paper_run(run_id: str) -> dict | None:
    return fetch_one("SELECT * FROM bts_paper_runs WHERE id = %s", (run_id,))


def load_paper_snapshots(run_id: str, limit: int = 10000) -> list[dict]:
    return fetch_all(
        "SELECT * FROM bts_paper_snapshots WHERE run_id = %s ORDER BY ts DESC LIMIT %s",
        (run_id, limit),
    )


def load_live_run(run_id: str) -> dict | None:
    return fetch_one("SELECT * FROM bts_live_runs WHERE id = %s", (run_id,))


def load_audit_log(run_id: str, limit: int = 200) -> list[dict]:
    return fetch_all(
        "SELECT * FROM bts_audit_log WHERE run_id = %s ORDER BY ts DESC LIMIT %s",
        (run_id, limit),
    )
