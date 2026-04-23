"""Tests for common job queue primitives (Redis not required — mocked)."""

from __future__ import annotations


def test_gen_job_id_format():
    from app.services.backtestsys_plugin.api.common import gen_job_id
    jid = gen_job_id("def")
    assert jid.startswith("def_")
    assert len(jid) == 4 + 10  # "def_" + 10 chars


def test_gen_job_id_unique():
    from app.services.backtestsys_plugin.api.common import gen_job_id
    ids = {gen_job_id("test") for _ in range(200)}
    assert len(ids) == 200  # extremely likely with 36^10 space


def test_utcnow_is_tz_aware():
    from app.services.backtestsys_plugin.api.common import utcnow
    n = utcnow()
    assert n.tzinfo is not None


def test_run_worker_loop_handles_handler_exceptions(monkeypatch):
    """Handler exceptions must not kill the loop (unless stop_on_error=True)."""
    from app.services.backtestsys_plugin.api import common

    class StubQueue:
        name = "test:stub"
        def __init__(self):
            self.calls = 0
        def blpop(self, timeout: int = 30):
            self.calls += 1
            if self.calls == 1:
                return "job_a"
            if self.calls == 2:
                return "job_b"
            raise KeyboardInterrupt()  # clean exit

    q = StubQueue()
    seen = []
    def handler(jid):
        seen.append(jid)
        if jid == "job_a":
            raise RuntimeError("boom")

    common.run_worker_loop(q, handler, stop_on_error=False)
    assert seen == ["job_a", "job_b"]


def test_run_worker_loop_stop_on_error():
    from app.services.backtestsys_plugin.api import common

    class StubQueue:
        name = "test:stub"
        def blpop(self, timeout: int = 30):
            return "job_x"

    def handler(jid):
        raise RuntimeError("stop")

    import pytest
    with pytest.raises(RuntimeError):
        common.run_worker_loop(StubQueue(), handler, stop_on_error=True)
