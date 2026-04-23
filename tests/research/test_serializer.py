"""Tests for serializer — dataclass / numpy / pandas → JSON-safe."""

from __future__ import annotations

import dataclasses
import math


def test_primitives_pass_through():
    from app.services.backtestsys_plugin.api.serializer import to_json_safe
    assert to_json_safe(None) is None
    assert to_json_safe(True) is True
    assert to_json_safe(42) == 42
    assert to_json_safe("hi") == "hi"
    assert to_json_safe(3.14) == 3.14


def test_nan_and_inf_become_none():
    from app.services.backtestsys_plugin.api.serializer import to_json_safe
    assert to_json_safe(float("nan")) is None
    assert to_json_safe(float("inf")) is None


def test_dataclass_recurses():
    from app.services.backtestsys_plugin.api.serializer import to_json_safe

    @dataclasses.dataclass
    class Inner:
        x: float

    @dataclasses.dataclass
    class Outer:
        name: str
        items: list

    o = Outer(name="hi", items=[Inner(x=1.0), Inner(x=2.0)])
    d = to_json_safe(o)
    assert d == {"name": "hi", "items": [{"x": 1.0}, {"x": 2.0}]}


def test_numpy_arrays_listified():
    import numpy as np
    from app.services.backtestsys_plugin.api.serializer import to_json_safe
    arr = np.array([1.0, 2.0, 3.0])
    assert to_json_safe(arr) == [1.0, 2.0, 3.0]


def test_pandas_series_listified():
    import pandas as pd
    from app.services.backtestsys_plugin.api.serializer import to_json_safe
    s = pd.Series([1, 2, 3])
    assert to_json_safe(s) == [1, 2, 3]
