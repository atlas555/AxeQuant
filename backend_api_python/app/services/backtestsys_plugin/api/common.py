"""Shared job/queue primitives reused across all research services.

Every research job (defense, autoresearch, paper, live) follows the pattern:
  1. Client POSTs payload → enqueue_job() returns job_id
  2. Worker blpops from Redis queue → process_job()
  3. Client polls GET /{job_id} → loads record from Postgres

This module provides the scaffolding. Each phase specializes via its own
`_service.py` module that imports from here.
"""

from __future__ import annotations

import os
import secrets
import string
from dataclasses import dataclass
from typing import Any, Callable, Optional


def gen_job_id(prefix: str = "job") -> str:
    """Generate a short, URL-safe job ID."""
    alphabet = string.ascii_lowercase + string.digits
    return f"{prefix}_" + "".join(secrets.choice(alphabet) for _ in range(10))


def get_redis():
    """Lazy-import redis client. Reads REDIS_URL from env."""
    import redis  # deferred — not required in test environments
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url)


@dataclass
class JobQueue:
    """Thin wrapper around a Redis list used as a FIFO job queue."""

    name: str

    def enqueue(self, job_id: str) -> None:
        get_redis().rpush(self.name, job_id)

    def blpop(self, timeout: int = 30) -> Optional[str]:
        """Block until a job lands or timeout. Returns job_id or None."""
        result = get_redis().blpop(self.name, timeout=timeout)
        if result is None:
            return None
        _, raw = result
        return raw.decode() if isinstance(raw, bytes) else str(raw)


# ── Named queues (one per phase) ────────────────────────────────────

DEFENSE_QUEUE = JobQueue("bts:defense:jobs")
AUTORESEARCH_QUEUE = JobQueue("bts:autoresearch:jobs")
PAPER_QUEUE = JobQueue("bts:paper:jobs")
LIVE_QUEUE = JobQueue("bts:live:jobs")


# ── Process-job framework (for workers) ─────────────────────────────

def run_worker_loop(queue: JobQueue, handler: Callable[[str], None], *,
                    stop_on_error: bool = False) -> None:
    """Standard worker main-loop.

    Handler is called with the popped job_id. Exceptions are logged but the
    loop continues by default (production workers should survive bad jobs).
    """
    import logging
    log = logging.getLogger(queue.name)
    log.info("Worker started on queue %s", queue.name)
    while True:
        try:
            job_id = queue.blpop(timeout=30)
            if job_id is None:
                continue  # idle heartbeat
            log.info("Processing %s", job_id)
            handler(job_id)
        except KeyboardInterrupt:
            log.info("Worker interrupted, exiting")
            break
        except Exception:  # noqa: BLE001 — top-level catch intentional
            log.exception("Job handler raised")
            if stop_on_error:
                raise


# ── DB helpers ──────────────────────────────────────────────────────

def utcnow():
    """Timezone-aware UTC now — pin to one definition across services."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
