"""Shared pytest fixtures + import shim for backtestsys_plugin tests.

QD's `app/services/__init__.py` eagerly imports backend services (yfinance,
psycopg2, etc.). For plugin-level tests we don't want those deps. The shim
installs minimal `app` and `app.services` package stubs before any plugin
imports, so we can test plugin code in isolation.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend_api_python"


def _install_package_shim() -> None:
    if "app" in sys.modules and getattr(sys.modules["app"], "__axequant_shim__", False):
        return
    pkg_app = types.ModuleType("app")
    pkg_app.__path__ = [str(BACKEND_ROOT / "app")]
    pkg_app.__axequant_shim__ = True
    pkg_svc = types.ModuleType("app.services")
    pkg_svc.__path__ = [str(BACKEND_ROOT / "app" / "services")]
    sys.modules["app"] = pkg_app
    sys.modules["app.services"] = pkg_svc
    sys.path.insert(0, str(BACKEND_ROOT))


_install_package_shim()


@pytest.fixture
def sample_ohlcv():
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(42)
    n = 800
    close = 50_000 + np.cumsum(rng.normal(0, 50, n))
    open_ = close + rng.normal(0, 10, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 20, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 20, n))
    volume = rng.uniform(100, 1000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )
