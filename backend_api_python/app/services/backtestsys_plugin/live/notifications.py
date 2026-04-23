"""Notification backends — Telegram, Feishu. Fail-silent if unconfigured.

Wired into Phase 5 urgent alerts (kill switch, drift, capital threshold).
Not required for core correctness — absence downgrades observability only.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def send_urgent(run, reason: str) -> None:
    """Dispatch to all configured channels. Best-effort."""
    msg = f"🚨 LIVE RUN {run.id}: {reason}"
    for fn in (_send_telegram, _send_feishu):
        try:
            fn(msg)
        except Exception:  # noqa: BLE001
            log.exception("%s failed", fn.__name__)


def _send_telegram(msg: str) -> None:
    import urllib.request
    bot = os.environ.get("LIVE_TELEGRAM_BOT")
    chat = os.environ.get("LIVE_TELEGRAM_CHAT")
    if not bot or not chat:
        return
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    data = json.dumps({"chat_id": chat, "text": msg}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def _send_feishu(msg: str) -> None:
    import json, urllib.request
    hook = os.environ.get("LIVE_FEISHU_WEBHOOK")
    if not hook:
        return
    data = json.dumps({"msg_type": "text", "content": {"text": msg}}).encode()
    req = urllib.request.Request(hook, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


# Ensure json is available for telegram path (lazy import bug fix)
import json  # noqa: E402
