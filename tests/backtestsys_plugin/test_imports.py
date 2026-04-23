"""Smoke tests: plugin package imports cleanly and discovers signals."""

from __future__ import annotations


def test_plugin_imports():
    from app.services.backtestsys_plugin.adapters.ctx_signals import attach_signals, _SignalProxy
    from app.services.backtestsys_plugin.signals.registry import SignalRegistry

    assert callable(attach_signals)
    assert _SignalProxy is not None
    assert SignalRegistry is not None


def test_signals_auto_discover():
    from app.services.backtestsys_plugin.signals.registry import SignalRegistry

    SignalRegistry.auto_discover()
    available = SignalRegistry.available()

    for required in ("asrband", "wavetrend", "atr", "ema", "rsi"):
        assert required in available, f"Missing required signal: {required}"


def test_vendor_version_file_exists():
    from pathlib import Path

    plugin_dir = Path(__file__).resolve().parents[2] / \
        "backend_api_python/app/services/backtestsys_plugin"
    version_file = plugin_dir / "VERSION"
    assert version_file.exists()
    sha = version_file.read_text().strip()
    assert len(sha) >= 7
