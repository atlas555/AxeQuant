"""Immutable audit log for live trading events.

Writes to `bts_audit_log` table. A DB-level BEFORE UPDATE/DELETE trigger
(installed by migrations/axequant_schema.sql) rejects mutation attempts.

Each event carries a SHA256 hash computed over (run_id, event_type, payload)
to detect tampering; hashes are chained lightly (prev_hash included in payload
if available) so deletion of a middle event is detectable by forward-verify.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


EventType = str  # "order_submitted" | "order_filled" | "kill_switch_fired" | ...


def log_event(run_id: str, event_type: EventType, payload: dict[str, Any],
              db_session) -> None:
    """Append an event. Commits immediately so the record is durable."""
    from app.services.backtestsys_plugin.api.common import utcnow
    from app.services.backtestsys_plugin.api.models import AuditLog

    prev_hash = _latest_hash_for(run_id, db_session)
    body = {
        "run_id": run_id, "event_type": event_type,
        "payload": payload, "prev_hash": prev_hash,
    }
    event_hash = _compute_hash(body)

    row = AuditLog(
        run_id=run_id, event_type=event_type,
        payload=payload, ts=utcnow(),
        hash=event_hash,
    )
    db_session.add(row)
    try:
        db_session.commit()
    except Exception:  # noqa: BLE001
        db_session.rollback()
        log.exception("audit log commit failed — this is serious")
        raise


def _latest_hash_for(run_id: str, db_session) -> str:
    from app.services.backtestsys_plugin.api.models import AuditLog
    row = (
        db_session.query(AuditLog)
        .filter_by(run_id=run_id)
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .first()
    )
    return row.hash if row else ""


def _compute_hash(body: dict) -> str:
    payload = json.dumps(body, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def verify_chain(run_id: str, db_session) -> tuple[bool, int | None]:
    """Replay hashes for a run. Returns (ok, first_broken_row_id_or_None)."""
    from app.services.backtestsys_plugin.api.models import AuditLog
    rows = (
        db_session.query(AuditLog)
        .filter_by(run_id=run_id)
        .order_by(AuditLog.ts.asc(), AuditLog.id.asc())
        .all()
    )
    prev_hash = ""
    for r in rows:
        body = {
            "run_id": r.run_id, "event_type": r.event_type,
            "payload": r.payload, "prev_hash": prev_hash,
        }
        expected = _compute_hash(body)
        if expected != r.hash:
            return False, r.id
        prev_hash = r.hash
    return True, None
