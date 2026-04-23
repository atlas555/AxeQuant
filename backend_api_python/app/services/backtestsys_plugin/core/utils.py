"""Shared utility functions for backTestSys."""

from __future__ import annotations

from typing import Any


def set_nested(d: dict, dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted key path.

    Example::

        set_nested(d, "strategy.leverage", 5)
        # equivalent to d["strategy"]["leverage"] = 5
    """
    keys = dotted_key.split(".")
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value
