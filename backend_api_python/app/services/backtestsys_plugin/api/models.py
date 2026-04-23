"""SQLAlchemy models for bts_* tables.

Kept in plugin namespace to isolate from upstream QD models. Uses upstream
`app.extensions.db` Session (standard Flask-SQLAlchemy pattern).

All tables prefixed `bts_` to avoid collisions with upstream schema.
"""

from __future__ import annotations

import json
from datetime import datetime

try:
    from app.extensions import db  # upstream QD DB singleton
except ImportError:  # tests / standalone use
    db = None


if db is not None:
    # Running inside Flask app context

    class DefenseReport(db.Model):
        __tablename__ = "bts_defense_reports"

        job_id = db.Column(db.String(32), primary_key=True)
        user_id = db.Column(db.Integer, nullable=True, index=True)
        strategy_id = db.Column(db.String(64), nullable=True, index=True)
        status = db.Column(db.String(16), nullable=False, default="queued")
        request = db.Column(db.JSON, nullable=False)
        result = db.Column(db.JSON, nullable=True)
        error = db.Column(db.Text, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        completed_at = db.Column(db.DateTime, nullable=True)

        def to_dict(self):
            return {
                "job_id": self.job_id, "status": self.status,
                "strategy_id": self.strategy_id,
                "request": self.request, "result": self.result,
                "error": self.error,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            }


    class AutoresearchReport(db.Model):
        __tablename__ = "bts_autoresearch_reports"

        job_id = db.Column(db.String(32), primary_key=True)
        user_id = db.Column(db.Integer, nullable=True, index=True)
        strategy_id = db.Column(db.String(64), nullable=True, index=True)
        status = db.Column(db.String(16), nullable=False, default="queued")
        request = db.Column(db.JSON, nullable=False)
        result = db.Column(db.JSON, nullable=True)
        error = db.Column(db.Text, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        completed_at = db.Column(db.DateTime, nullable=True)

        def to_dict(self):
            return {
                "job_id": self.job_id, "status": self.status,
                "strategy_id": self.strategy_id,
                "request": self.request, "result": self.result,
                "error": self.error,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            }


    class AutoresearchCandidate(db.Model):
        __tablename__ = "bts_autoresearch_candidates"

        id = db.Column(db.Integer, primary_key=True)
        job_id = db.Column(db.String(32), db.ForeignKey("bts_autoresearch_reports.job_id"), index=True)
        rank = db.Column(db.Integer, nullable=False)
        params = db.Column(db.JSON, nullable=False)
        oos_sharpe = db.Column(db.Float, nullable=True, index=True)
        n_trades = db.Column(db.Integer, nullable=True)
        defense_job_id = db.Column(db.String(32), nullable=True)
        verdict = db.Column(db.String(16), nullable=True)

        def to_dict(self):
            return {"id": self.id, "rank": self.rank, "params": self.params,
                    "oos_sharpe": self.oos_sharpe, "n_trades": self.n_trades,
                    "defense_job_id": self.defense_job_id, "verdict": self.verdict}


    class PaperRun(db.Model):
        __tablename__ = "bts_paper_runs"

        id = db.Column(db.String(32), primary_key=True)
        user_id = db.Column(db.Integer, nullable=True, index=True)
        strategy_id = db.Column(db.String(64), nullable=True, index=True)
        candidate_id = db.Column(db.Integer, nullable=True)
        params = db.Column(db.JSON, nullable=False)
        exchange = db.Column(db.String(32), nullable=False)
        testnet = db.Column(db.Boolean, nullable=False, default=True)
        initial_capital = db.Column(db.Float, nullable=False)
        status = db.Column(db.String(16), nullable=False, default="starting")
        config = db.Column(db.JSON, nullable=True)
        started_at = db.Column(db.DateTime, nullable=True)
        stopped_at = db.Column(db.DateTime, nullable=True)
        drift_violations = db.Column(db.Integer, nullable=False, default=0)


    class PaperSnapshot(db.Model):
        __tablename__ = "bts_paper_snapshots"

        id = db.Column(db.Integer, primary_key=True)
        run_id = db.Column(db.String(32), db.ForeignKey("bts_paper_runs.id"), index=True)
        ts = db.Column(db.DateTime, nullable=False, index=True)
        equity = db.Column(db.Float, nullable=False)
        position_size = db.Column(db.Float, nullable=True)
        position_side = db.Column(db.String(8), nullable=True)


    class LiveRun(db.Model):
        __tablename__ = "bts_live_runs"

        id = db.Column(db.String(32), primary_key=True)
        user_id = db.Column(db.Integer, nullable=True, index=True)
        paper_run_id = db.Column(db.String(32), nullable=True)
        strategy_id = db.Column(db.String(64), nullable=True, index=True)
        params = db.Column(db.JSON, nullable=False)
        exchange = db.Column(db.String(32), nullable=False)
        capital = db.Column(db.Float, nullable=False)
        status = db.Column(db.String(16), nullable=False, default="starting")
        config = db.Column(db.JSON, nullable=True)
        qualification = db.Column(db.JSON, nullable=True)
        started_at = db.Column(db.DateTime, nullable=True)
        killed_at = db.Column(db.DateTime, nullable=True)
        kill_reason = db.Column(db.Text, nullable=True)


    class AuditLog(db.Model):
        __tablename__ = "bts_audit_log"

        id = db.Column(db.Integer, primary_key=True)
        run_id = db.Column(db.String(32), nullable=False, index=True)
        event_type = db.Column(db.String(32), nullable=False, index=True)
        payload = db.Column(db.JSON, nullable=False)
        ts = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
        hash = db.Column(db.String(64), nullable=False)
