"""Shared conftest for research-service tests. Uses the same package shim
as backtestsys_plugin tests (avoids pulling yfinance etc.)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend_api_python"


def _install_shim():
    if "app" in sys.modules and getattr(sys.modules["app"], "__axequant_shim__", False):
        return
    pkg_app = types.ModuleType("app"); pkg_app.__path__ = [str(BACKEND_ROOT / "app")]
    pkg_app.__axequant_shim__ = True
    pkg_svc = types.ModuleType("app.services"); pkg_svc.__path__ = [str(BACKEND_ROOT / "app" / "services")]
    sys.modules["app"] = pkg_app
    sys.modules["app.services"] = pkg_svc
    sys.path.insert(0, str(BACKEND_ROOT))


_install_shim()
